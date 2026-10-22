import json
import tempfile
import unittest
from pathlib import Path

from contractedge.model.money import MoneyBudget, MoneyBudgetExceeded


class MoneyBudgetTests(unittest.TestCase):
    def test_concurrent_reservations_share_one_hard_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as tmp:
            budget = MoneyBudget(Path(tmp)/'ledger.json', limit_cny='0.01', model='test', input_cny_per_million='2', output_cny_per_million='8')
            def attempt(_):
                try:
                    budget.reserve('test', 100, 100)
                    return True
                except MoneyBudgetExceeded:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(attempt,range(30))),10)
            self.assertEqual(budget.summary()['committed_micro_cny'],10000)

    def test_reservations_survive_restart_and_block_before_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.json'
            budget = MoneyBudget(path, limit_cny='0.01', model='test', input_cny_per_million='2', output_cny_per_million='8')
            reservation = budget.reserve('test', 1000, 1000)
            restarted = MoneyBudget(path, limit_cny='0.01', model='test', input_cny_per_million='2', output_cny_per_million='8')
            with self.assertRaises(MoneyBudgetExceeded):
                restarted.reserve('test', 1, 1)
            restarted.settle(reservation, {'prompt_tokens': 100, 'completion_tokens': 100})
            restarted.reserve('test', 100, 100)
            ledger = json.loads(path.read_text())
            self.assertEqual(ledger['calls'][0]['charged_micro_cny'], 1000)
            self.assertEqual(ledger['calls'][1]['status'], 'RESERVED')

    def test_unverified_usage_and_wrong_model_cannot_release_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            budget = MoneyBudget(Path(tmp)/'ledger.json', limit_cny='1', model='test', input_cny_per_million='2', output_cny_per_million='8')
            with self.assertRaises(ValueError):
                budget.reserve('another-model', 1, 1)
            reservation = budget.reserve('test', 1000, 1000)
            budget.settle(reservation, None)
            self.assertEqual(budget.summary()['committed_micro_cny'], 10000)
            with self.assertRaises(ValueError):
                MoneyBudget(budget.path, limit_cny='2', model='test', input_cny_per_million='2', output_cny_per_million='8')

    def test_api_port_reserves_before_network_and_settles_reported_usage(self):
        import os
        from unittest.mock import patch
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from contractedge.model.provider import ModelRequest, OpenAICompatibleProvider
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw = json.dumps({'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
                                  'usage':{'prompt_tokens':10,'completion_tokens':2}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        os.environ['CE_TEST_KEY']='test-only'
        self.addCleanup(os.environ.pop, 'CE_TEST_KEY')
        with tempfile.TemporaryDirectory() as tmp:
            budget = MoneyBudget(Path(tmp)/'ledger.json', limit_cny='0.01', model='test', input_cny_per_million='2', output_cny_per_million='8')
            provider = OpenAICompatibleProvider(f'http://127.0.0.1:{server.server_port}', 'CE_TEST_KEY', money_budget=budget, thinking='enabled', reasoning_effort='low')
            request = ModelRequest('test','probe',({'role':'user','content':'Return JSON.'},),32)
            sync_calls=[]
            real_sync=os.fsync
            def sync(fd):
                real_sync(fd)
                sync_calls.append(fd)
            with patch('os.fsync', side_effect=sync):
                provider.complete_json(request)
            self.assertGreaterEqual(len(sync_calls),4, 'reservation and settlement each require file and directory durability')
            self.assertEqual(budget.summary()['committed_micro_cny'],36)
            with self.assertRaises(MoneyBudgetExceeded):
                provider.complete_json(ModelRequest('test','probe',request.messages,2000))
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['thinking'], {'type': 'enabled'})
            self.assertEqual(calls[0]['reasoning_effort'], 'low')
