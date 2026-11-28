"""Frozen M1 requirement extraction and mechanical handoff."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal
import hashlib
from importlib.resources import files
import json
import os
import sys
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from contractedge.model.admission import require_context
from contractedge.model.budget import BudgetedProvider
from contractedge.model.money import MoneyBudget
from contractedge.model.provider import ModelRequest, OpenAICompatibleProvider, ProviderResponseError
from contractedge.tokenizers import utf8_byte_token_bound


FIELDS = {"requirement", "type", "conditions", "relation", "sources", "evidence_status"}
SOURCE_FIELDS = {"document", "start_line", "end_line", "quote"}


def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def read(path):
    return json.loads(path.read_text())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _quoted(source, documents):
    if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
        return False
    key, start, end, quote = (source[k] for k in ('document', 'start_line', 'end_line', 'quote'))
    if (not isinstance(key, str) or key not in documents or type(start) is not int
            or type(end) is not int or not isinstance(quote, str) or not quote.strip()):
        return False
    doc = documents[key]
    lines = doc['text'].splitlines(keepends=True)
    first = doc['first_line']
    if not first <= start <= end < first + len(lines):
        return False
    snippet = ''.join(lines[start - first:end - first + 1])
    offset = snippet.find(quote)
    while offset != -1:
        quote_start = start + snippet.count('\n', 0, offset)
        quote_end = quote_start + quote.rstrip('\n').count('\n')
        if (quote_start, quote_end) == (start, end):
            return True
        offset = snippet.find(quote, offset + 1)
    return False


LOCATED_CITATION = frozenset({'ORIGINAL_VALID', 'RECOVERED_UNIQUE'})


class FrozenCitationLocator:
    """Use only exact text from a hash-pinned public packet."""

    def __init__(self, packet_bytes, expected_sha256):
        self._verified = sha(packet_bytes) == expected_sha256
        self._packet = json.loads(packet_bytes) if self._verified else None
        self._documents = {}
        if self._verified:
            for document in self._packet['documents']:
                key = document['id']
                if key in self._documents and self._documents[key] != document:
                    self._verified = False
                    break
                self._documents[key] = document

    def bind(self, packet):
        identity_matches = self._verified and packet == self._packet
        return lambda source: self._locate(source, identity_matches)

    def _locate(self, source, identity_matches):
        result = {'original': source, 'resolved': None}
        if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
            return {**result, 'status': 'INVALID_CITATION'}
        key, start, end, quote = (source[k] for k in ('document', 'start_line', 'end_line', 'quote'))
        if (not isinstance(key, str) or type(start) is not int or type(end) is not int
                or not isinstance(quote, str) or not quote.strip()):
            return {**result, 'status': 'INVALID_CITATION'}
        if not identity_matches:
            return {**result, 'status': 'IDENTITY_MISMATCH'}
        if key not in self._documents:
            return {**result, 'status': 'DOCUMENT_MISSING'}
        if _quoted(source, self._documents):
            return {**result, 'status': 'ORIGINAL_VALID',
                    'resolved': {'document': key, 'start_line': start, 'end_line': end}}

        # Other documents may be overlapping fragments of the same original file.
        # Searching by document ID avoids counting their copies as extra occurrences.
        text = self._documents[key]['text']
        first = self._documents[key]['first_line']
        offset = text.find(quote)
        if offset == -1:
            return {**result, 'status': 'QUOTE_NOT_FOUND'}
        if text.find(quote, offset + 1) != -1:
            return {**result, 'status': 'AMBIGUOUS'}
        start = first + text.count('\n', 0, offset)
        end = start + quote.rstrip('\n').count('\n')
        resolved = {'document': key, 'start_line': start, 'end_line': end}
        if not _quoted({**resolved, 'quote': quote}, self._documents):
            return {**result, 'status': 'COORDINATES_UNREPRESENTABLE'}
        return {**result, 'status': 'RECOVERED_UNIQUE', 'resolved': resolved}


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON key')
    return result


def classify(content, finish, packet, *, citation_locator=None):
    """Apply identical mechanical checks. The model's semantic text is preserved."""
    if finish in {'length', 'max_tokens'}:
        return {'status': 'TRUNCATED', 'usable_rows': [], 'rows': []}
    if not isinstance(content, str) or not content.strip():
        return {'status': 'EMPTY', 'usable_rows': [], 'rows': []}
    try:
        obj = json.loads(content, object_pairs_hook=_unique_object)
    except ValueError:
        return {'status': 'INVALID_JSON', 'usable_rows': [], 'rows': []}
    if (not isinstance(obj, dict) or set(obj) != {'requirements', 'unresolved_questions'}
            or not isinstance(obj['requirements'], list) or not isinstance(obj['unresolved_questions'], list)
            or any(not isinstance(question, str) for question in obj['unresolved_questions'])):
        return {'status': 'INVALID_FORMAT', 'usable_rows': [], 'rows': []}
    docs = {doc['id']: doc for doc in packet['documents']}
    locate = citation_locator.bind(packet) if citation_locator is not None else None
    citation_locations = {}
    rows, usable = [], []
    for index, row in enumerate(obj['requirements']):
        issues = []
        if (not isinstance(row, dict) or set(row) != FIELDS
                or not isinstance(row['requirement'], str) or not row['requirement'].strip()
                or row['type'] not in ('change', 'preserve')
                or row['evidence_status'] not in ('clear', 'pending')
                or not isinstance(row['sources'], list)
                or any(row[key] is not None and (not isinstance(row[key], str) or not row[key].strip())
                       for key in ('conditions', 'relation'))):
            issues.append('INVALID_FIELDS')
        else:
            if locate is None:
                citation_mismatch = any(not _quoted(source, docs) for source in row['sources'])
            else:
                locations = [locate(source) for source in row['sources']]
                citation_locations[index] = locations
                citation_mismatch = any(location['status'] not in LOCATED_CITATION for location in locations)
            if citation_mismatch:
                issues.append('CITATION_MISMATCH')
            if row['evidence_status'] == 'clear':
                if not row['sources']:
                    issues.append('NO_PUBLIC_CITATION')
                if row['conditions'] is None:
                    issues.append('MISSING_APPLICABILITY')
                if row['relation'] is None:
                    issues.append('MISSING_EXPECTATION')
            elif not issues:
                rows.append({'index': index, 'status': 'PENDING', 'issues': []})
                continue
        rows.append({'index': index, 'status': 'REJECTED' if issues else 'MECHANICALLY_ACCEPTED', 'issues': issues})
        if not issues:
            usable.append(index)
    status = 'MECHANICALLY_USABLE' if usable else 'NO_MECHANICALLY_USABLE_CLEAR_ROWS'
    result = {'status': status, 'usable_rows': usable, 'rows': rows,
              'unresolved_questions': obj['unresolved_questions']}
    if locate is not None:
        result['citation_locations'] = citation_locations
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RecordedResponse:
    def __init__(self, response, stream):
        self.response = response
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.response, name)

    def read(self, size=None):
        # HTTPResponse uses None for an unbounded read that still dechunks.
        data = self.response.read(size)
        self.stream.write(data)
        self.stream.flush()
        return data


class AuditedProvider(OpenAICompatibleProvider):
    """Keep the original parser and budget, and capture the HTTP reader output."""
    def __init__(self, profile, endpoint, mode, money):
        url = urlsplit(endpoint)
        if mode == 'fixture':
            if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.netloc != f'127.0.0.1:{url.port}' or url.path:
                raise ValueError('Fixture transport accepts only an HTTP loopback origin')
        elif endpoint != 'https://api.deepseek.com':
            raise ValueError('The formal profile requires the official provider host')
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.slot = None
        self.mode = mode
        super().__init__(endpoint, 'DEEPSEEK_API_KEY', profile['request_seconds'],
                         money_budget=money, model_identity_policy='require_match')

    def _api_key(self):
        return 'fixture-without-provider-credentials' if self.mode == 'fixture' else super()._api_key()

    @contextmanager
    def _open_request(self, request, timeout):
        start = time.monotonic()
        with self.slot.joinpath('response.body').open('xb') as stream:
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    save(self.slot / 'http.json', {'status': response.status, 'timeout_seconds': timeout})
                    yield RecordedResponse(response, stream)
            except HTTPError as exc:
                save(self.slot / 'http.json', {'status': exc.code, 'timeout_seconds': timeout})
                with exc:
                    stream.write(exc.read(1024 * 1024))
                raise
            finally:
                save(self.slot / 'transport_elapsed.json', {'elapsed_seconds': time.monotonic() - start})


def _null_content_reply(slot):
    raw = read(slot / 'response.body')
    choices = raw.get('choices') if isinstance(raw, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get('message')
    return isinstance(message, dict) and 'content' in message and message['content'] is None


def _known_usage(metadata):
    usage = metadata.get('usage')
    return (isinstance(usage, dict)
            and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens','completion_tokens')))


CONFIG_SCHEMA = 'contractedge.m1_experiment.v1'
MODEL_PROFILE = {
    'model': 'deepseek-flash',
    'max_output_tokens': 8192,
    'temperature': 0,
    'thinking': 'disabled',
    'request_seconds': 120,
    'context_tokens': 65536,
}


def load_config(path):
    config = read(path)
    mode = config.get('m1')
    if config.get('schema') != CONFIG_SCHEMA:
        raise ValueError('Unsupported M1 experiment configuration')
    if mode == 'disabled':
        if set(config) != {'schema', 'm1', 'live_profile'}:
            raise ValueError('The baseline delegates directly to the common Live profile')
    elif mode not in ('ordinary-control', 'evidence-roles') or config != {
            'schema': CONFIG_SCHEMA, 'm1': mode, **MODEL_PROFILE}:
        raise ValueError('M1 mode or frozen model profile differs')
    return config


def prompt(config):
    group = {'ordinary-control': 'A', 'evidence-roles': 'B'}[config['m1']]
    return files(__package__).joinpath(f'm1-{group}.txt').read_bytes().decode('utf-8')


def request_for(packet, config):
    return ModelRequest(
        config['model'], 'contractedge-m1-evidence-role-v1',
        ({'role': 'system', 'content': prompt(config)},
         {'role': 'user', 'content': json.dumps(packet, ensure_ascii=False, separators=(',', ':'))}),
        config['max_output_tokens'], config['temperature'], thinking=config['thinking'],
    )


def project(content, mechanical):
    """Project accepted rows and uncertainty with the original field values."""
    if 'unresolved_questions' not in mechanical:
        return {'clear_requirements': [], 'pending_requirements': [], 'unresolved_questions': []}
    requirements = json.loads(content, object_pairs_hook=_unique_object)['requirements']
    return {
        'clear_requirements': [requirements[i] for i in mechanical['usable_rows']],
        'pending_requirements': [
            requirements[row['index']] for row in mechanical['rows'] if row['status'] == 'PENDING'
        ],
        'unresolved_questions': mechanical['unresolved_questions'],
    }


def process(packet_bytes, packet_sha256, content, finish_reason):
    if sha(packet_bytes) != packet_sha256:
        raise ValueError('The public packet differs from its frozen identity')
    mechanical = classify(content, finish_reason, json.loads(packet_bytes),
                          citation_locator=FrozenCitationLocator(packet_bytes, packet_sha256))
    return mechanical, project(content, mechanical)


def prepare(packet_bytes, packet_sha256, config):
    if sha(packet_bytes) != packet_sha256:
        raise ValueError('The public packet differs from its frozen identity')
    request = request_for(json.loads(packet_bytes), config)
    adapter = AuditedProvider(config, 'https://api.deepseek.com', 'paid', None)
    budget = BudgetedProvider(adapter, utf8_byte_token_bound, max_tokens=config['context_tokens'],
                              max_seconds=config['request_seconds'], max_calls=1)
    quote = budget.quote_request(request)
    require_context(quote, config['context_tokens'])
    return request, quote.prepared.body


def run(args, config, packet_bytes):
    req, body = prepare(packet_bytes, args.packet_sha256, config)
    mode = 'fixture' if args.fixture_origin else 'paid'
    out = args.out.resolve()
    if mode == 'paid':
        approval = read(args.authorization)
        if (approval.get('schema') != 'contractedge.m1_single_request.v1'
                or approval.get('status') != 'AUTHORIZED'
                or approval['request_sha256'] != sha(body)
                or approval['attempt_directory'] != str(out)):
            raise ValueError('Authorization must identify these exact request bytes and the attempt directory')
        ledger = Path(approval['ledger'])
        if not ledger.is_absolute() or ledger.resolve().is_relative_to(out):
            raise ValueError("Authorization must name an independent cumulative ledger")
        if not ledger.is_file() or sha(ledger.read_bytes()) != approval["ledger_sha256"]:
            raise ValueError('Authorized ledger digest differs')
        cap = Decimal(approval['authorized_limit_cny'])
    else:
        ledger, cap = out / 'money.json', Decimal('1')
    # A pre-existing attempt is never used for a second send.
    out.mkdir(mode=0o700, exist_ok=False)
    with (out / 'request.body').open('xb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    save(out / 'attempt.json', {'request_sha256': sha(body), 'packet_sha256': args.packet_sha256,
                               'usage_origin': 'SYNTHETIC' if mode == 'fixture' else 'PROVIDER_REPORTED'})
    money = MoneyBudget(ledger, limit_cny=cap, model=config['model'],
                        input_cny_per_million='2', output_cny_per_million='8')
    adapter = AuditedProvider(config, args.fixture_origin or 'https://api.deepseek.com', mode, money)
    adapter.slot = out
    budget = BudgetedProvider(adapter, utf8_byte_token_bound, max_tokens=config['context_tokens'],
                              max_seconds=config['request_seconds'], max_calls=1,
                              reconcile_observed_input=True, reconcile_observed_output=True,
                              on_update=lambda rows: save(out / 'requests.json', rows))
    quote = budget.quote_request(req)
    if quote.prepared.body != body:
        raise ValueError('Adapter serialization differs from the admitted request')
    outcome = {**read(out / 'attempt.json'), 'status': 'TECHNICAL_STOP'}
    try:
        response = budget.complete_quoted(req, quote)
    except ProviderResponseError as exc:
        response = exc.response
        if not (response.metadata.get('model_identity_verified') is True
                and response.metadata.get('raw_response_content', False) is None
                and _null_content_reply(out)):
            outcome['error'] = adapter.safe_error_message(exc)
    except Exception as exc:
        response = None
        outcome['error'] = adapter.safe_error_message(exc)
    if response is not None:
        save(out / 'response.json', {'content': response.content, 'metadata': response.metadata})
        usage = response.metadata.get('usage')
        if not _known_usage(response.metadata):
            outcome.setdefault('error', 'USAGE_UNKNOWN')
        elif usage['prompt_tokens'] > len(body) + 1024 or usage['completion_tokens'] > config['max_output_tokens']:
            outcome.setdefault('error', 'USAGE_EXCEEDS_RESERVATION')
        if 'error' not in outcome:
            mechanical, handoff = process(packet_bytes, args.packet_sha256, response.content,
                                          response.metadata.get('finish_reason'))
            save(out / 'mechanical.json', mechanical)
            save(out / 'handoff.json', handoff)
            outcome['status'] = mechanical['status']
    outcome['money'] = money.summary()
    save(out / 'result.json', outcome)
    return 2 if outcome['status'] == 'TECHNICAL_STOP' else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Live receives exactly its original arguments plus the selected original profile.
    if argv and argv[0] == 'live':
        parser = argparse.ArgumentParser()
        parser.add_argument('--config', type=Path, required=True)
        args, remaining = parser.parse_known_args(argv[1:])
        config = load_config(args.config)
        if config['m1'] != 'disabled' or any(
                word == '--profile' or word.startswith('--profile=') for word in remaining):
            parser.error('Live requires the disabled M1 config and its original common profile')
        from contractedge.graph_repair.harness.common_entry import main as common_live
        return common_live(['--profile', str(args.config.parent / config['live_profile']), *remaining])

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'run', 'replay'):
        entry = commands.add_parser(command)
        entry.add_argument('--packet', type=Path, required=True)
        entry.add_argument('--packet-sha256', required=True)
        if command != 'replay':
            entry.add_argument('--config', type=Path, required=True)
        if command == 'run':
            entry.add_argument('--out', type=Path, required=True)
            transport = entry.add_mutually_exclusive_group(required=True)
            transport.add_argument('--fixture-origin')
            transport.add_argument('--authorization', type=Path)
        elif command == 'replay':
            entry.add_argument('--response', type=Path, required=True)
    args = parser.parse_args(argv)
    packet_bytes = args.packet.read_bytes()
    if args.command == 'replay':
        response = read(args.response)
        mechanical, handoff = process(packet_bytes, args.packet_sha256, response['content'],
                                      response['metadata'].get('finish_reason'))
        print(json.dumps({'mechanical': mechanical, 'handoff': handoff}, ensure_ascii=False))
        return 0
    config = load_config(args.config)
    if config['m1'] == 'disabled':
        parser.error('Use the live subcommand with the disabled M1 config')
    if args.command == 'prepare':
        _, body = prepare(packet_bytes, args.packet_sha256, config)
        sys.stdout.buffer.write(body)
        return 0
    return run(args, config, packet_bytes)


if __name__ == '__main__':
    raise SystemExit(main())
