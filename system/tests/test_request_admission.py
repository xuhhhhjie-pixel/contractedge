import hashlib
import json
import unittest
from types import SimpleNamespace

from contractedge.model.admission import complete_quoted, context_fits, quote_request
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import ModelRequest, ModelResponse, OpenAICompatibleProvider


def request():
    return ModelRequest('fixture', 'admission.v1', ({'role': 'user', 'content': 'hi'},), 3,
                        metadata={'attempt': 1}, output_schema={'type': 'object',
                            'properties': {'answer': {'type': 'string'}},
                            'required': ['answer'], 'additionalProperties': False})


class PreparedPort:
    def __init__(self):
        self.prepares = 0
        self.dispatches = 0
        self.seen = None
        self.error = None

    def prepare_request(self, request):
        self.prepares += 1
        body = json.dumps({'sequence': self.prepares, 'schema': request.output_schema}).encode()
        return SimpleNamespace(body=body, overhead='schema', metadata={'provider': 'fixture'},
            audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})

    def request_overhead(self, request):
        raise AssertionError('A prepared request already owns its exact overhead')

    def complete_prepared_json(self, request, prepared):
        self.dispatches += 1
        self.seen = prepared
        if self.error:
            raise self.error
        return ModelResponse('{}', {})

    def complete_json(self, request):
        raise AssertionError('Prepared requests must not be prepared or sent again')


class RequestAdmissionTests(unittest.TestCase):
    def test_quote_is_pure_and_dispatch_reuses_exact_prepared_object(self):
        provider = PreparedPort()
        value = request()
        quote = quote_request(provider, value, len)
        self.assertEqual((quote.input_tokens, quote.output_tokens, quote.overhead_tokens,
                          quote.total_tokens), (8, 3, 6, 11))
        self.assertEqual((provider.prepares, provider.dispatches), (1, 0))
        complete_quoted(provider, value, quote)
        self.assertIs(provider.seen, quote.prepared)
        self.assertEqual((provider.prepares, provider.dispatches), (1, 1))

    def test_input_and_model_context_have_distinct_inclusive_boundaries(self):
        quote = quote_request(PreparedPort(), request(), len)
        self.assertTrue(context_fits(quote, 8, 11))
        self.assertFalse(context_fits(quote, 7, 11))
        self.assertFalse(context_fits(quote, 8, 10))
        self.assertTrue(context_fits(quote, 8))

    def test_mutated_request_or_other_owner_cannot_dispatch_a_quote(self):
        provider, value = PreparedPort(), request()
        quote = quote_request(provider, value, len)
        with self.assertRaises(ValueError):
            complete_quoted(PreparedPort(), value, quote)
        value.output_schema['properties'] = {}
        with self.assertRaises(ValueError):
            complete_quoted(provider, value, quote)
        self.assertEqual(provider.dispatches, 0)

    def test_legacy_provider_with_only_overhead_hook_still_works(self):
        class LegacyPort:
            calls = 0
            def request_overhead(self, request):
                return 'schema'
            def complete_json(self, request):
                self.calls += 1
                return ModelResponse('{}', {})
        provider, value = LegacyPort(), request()
        quote = quote_request(provider, value, len)
        self.assertEqual(quote.input_tokens, 8)
        self.assertIsNone(quote.prepared)
        complete_quoted(provider, value, quote)
        self.assertEqual(provider.calls, 1)

    def test_actual_native_schema_preparation_occurs_once_without_http(self):
        class CapturingNativePort(OpenAICompatibleProvider):
            formats = 0
            def _format(self, request):
                self.formats += 1
                return super()._format(request)
            def complete_json(self, request):
                self.seen = self._prepared_for(request)
                return ModelResponse('{}', {})
        provider = CapturingNativePort('https://unused.invalid', 'UNUSED',
            output_mode='json_schema', supports_json_schema=True)
        value = request()
        quote = quote_request(provider, value, len)
        self.assertGreater(quote.overhead_tokens, 0)
        complete_quoted(provider, value, quote)
        self.assertEqual(provider.formats, 1)
        self.assertIs(provider.seen, quote.prepared)

    def test_negative_message_or_schema_count_is_rejected(self):
        for negative in ('hi', 'schema'):
            with self.subTest(negative=negative), self.assertRaises(ValueError):
                quote_request(PreparedPort(), request(), lambda text: -1 if text == negative else len(text))

    def test_quoted_prepared_bytes_cannot_be_replaced(self):
        provider, value = PreparedPort(), request()
        quote = quote_request(provider, value, len)
        quote.prepared.body = b'changed after quote'
        with self.assertRaises(ValueError):
            complete_quoted(provider, value, quote)
        self.assertEqual(provider.dispatches, 0)


class BudgetedAdmissionTests(unittest.TestCase):
    def test_quote_has_no_reservation_and_dispatch_accounts_once(self):
        provider, value = PreparedPort(), request()
        updates = []
        budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=60,
            on_update=lambda calls: updates.append(len(calls)))
        quote = quote_request(budget, value, len)
        self.assertEqual((budget.reserved_tokens, budget.calls, updates), (0, [], []))
        complete_quoted(budget, value, quote)
        self.assertEqual((provider.prepares, provider.dispatches), (1, 1))
        self.assertEqual((budget.reserved_tokens, len(budget.calls)), (11, 1))
        self.assertEqual(budget.calls[0]['prompt_tokens_reserved'], 8)
        self.assertEqual(budget.calls[0]['transport_schema_tokens_reserved'], 6)
        # The reservation, the pre-dispatch unknown marker and the final
        # response settlement are each durable callback points.
        self.assertEqual(updates, [1, 1, 1])

    def test_legacy_complete_json_uses_the_same_quote_path(self):
        provider = PreparedPort()
        budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=60)
        budget.complete_json(request())
        self.assertEqual((provider.prepares, provider.dispatches), (1, 1))
        self.assertEqual(budget.reserved_tokens, 11)

    def test_global_budget_rejection_never_dispatches_or_records_new_call(self):
        provider, value = PreparedPort(), request()
        budget = BudgetedProvider(provider, len, max_tokens=10, max_seconds=60)
        quote = quote_request(budget, value, len)
        with self.assertRaises(BudgetExceeded):
            complete_quoted(budget, value, quote)
        self.assertEqual((budget.reserved_tokens, budget.calls, provider.dispatches), (0, [], 0))

    def test_mutation_is_rejected_before_global_reservation(self):
        provider, value = PreparedPort(), request()
        budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=60)
        quote = quote_request(budget, value, len)
        value.metadata['attempt'] = 2
        with self.assertRaises(ValueError):
            complete_quoted(budget, value, quote)
        self.assertEqual((budget.reserved_tokens, budget.calls, provider.dispatches), (0, [], 0))

    def test_legacy_request_mutated_after_reservation_is_not_dispatched(self):
        class LegacyPort:
            calls = 0
            def complete_json(self, request):
                self.calls += 1
                return ModelResponse('{}', {})
        provider, value = LegacyPort(), request()
        def persist(calls):
            value.messages[0]['content'] = 'unquoted content'
        budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=60, on_update=persist)
        with self.assertRaises(ValueError):
            budget.complete_json(value)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(len(budget.calls), 1)
        self.assertEqual(budget.reserved_tokens, 5)
        self.assertEqual(budget.calls[0]['request']['messages'][0]['content'], 'hi')

    def test_unknown_transport_keeps_reservation_and_durable_updates(self):
        provider, value = PreparedPort(), request()
        provider.error = TimeoutError('unknown response')
        snapshots = []
        budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=60,
            on_update=lambda calls: snapshots.append([dict(call) for call in calls]))
        quote = quote_request(budget, value, len)
        with self.assertRaises(TimeoutError):
            complete_quoted(budget, value, quote)
        self.assertEqual((provider.prepares, provider.dispatches, budget.reserved_tokens), (1, 1, 11))
        self.assertEqual(len(snapshots), 3)
        self.assertNotIn('error', snapshots[0][0])
        self.assertEqual(snapshots[1][0]['dispatch_state'], 'response_unknown')
        self.assertIn('TimeoutError', snapshots[2][0]['error'])
        self.assertIn('transport_request', snapshots[0][0])


if __name__ == '__main__':
    unittest.main()
