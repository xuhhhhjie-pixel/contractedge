"""Replay real public RPC requests through recorded/current guards, zero HTTP."""
from pathlib import Path
from dataclasses import asdict
import hashlib
import importlib.util
import json
import sys

SYSTEM = Path('/home/xhj/contractedge-cleancheck-v060-ordering-20261027/system')
OLD = Path('/home/xhj/contractedge-planning-branch-continuity-20261026/system')
ROOT = SYSTEM / '.superpowers/sdd/2026-10-27-context-limit-closure/replay'
CASE = Path('/home/xhj/contractedge-fixed46-source060-20261027/runs/methods/fixed46-v060-order-20261027-bcd9a8d56a4e00f2f902f406')
STATE = CASE / '.isolated-2ed9e1d52cfc4556bc09255c4fc755d9'
OUT = SYSTEM / 'outputs/context-limit-closure-20261027'
sys.path[:0] = [str(SYSTEM / 'scripts'), str(SYSTEM / 'src')]
from contractedge.model.provider import ModelRequest, ModelResponse
from contractedge.model.admission import quote_request, context_fits
from contractedge.tokenizers import utf8_byte_token_bound
from fixed_panel import verify_panel, verified_records
import isolated_protocol as rpc


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class FinitePort:
    timeout_seconds = 2400
    def __init__(self): self.dispatches = 0
    def request_overhead(self, request): return ''
    def complete_json(self, request):
        self.dispatches += 1
        return ModelResponse('{}', {'scope': 'Finite public replay; no HTTP'})


ROOT.mkdir(exist_ok=False)
ledger = Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json')
before = sha(ledger)
panel_path = Path('/home/xhj/contractedge-fixed46-source060-20261027/panel.json')
panel = verify_panel(panel_path); rows = verified_records(panel_path, panel)
config = json.loads(Path(panel['variants']['candidate']['config']).read_text())
calls_path = next((STATE / 'child').glob('*/model_calls.json'))
calls = json.loads(calls_path.read_text())
assert len(calls) == 6 and len(json.loads((STATE / 'parent-model-calls.json').read_text())) == 5
last = calls[-1]; assert last.get('response_content') is None
assert last['request_metadata']['planning_recovery'] == 'REASONING_STARVED_JSON_DISABLE_THINKING'
pending = [p for p in (STATE / 'method-journal').glob('*.json') if json.loads(p.read_text())['status'] != 'DONE']
assert len(pending) == 1 and json.loads(pending[0].read_text())['op'] == 'model.complete'
original_files = [STATE/'receipt.json', pending[0], calls_path, STATE/'parent-model-calls.json']
original_hashes = {str(p): sha(p) for p in original_files}
old = load(OLD / 'scripts/run_isolated_method.py', 'old_context_guard')
new = load(SYSTEM / 'scripts/run_isolated_method.py', 'new_context_guard')
results = []
for name, module in [('recorded', old), ('corrected', new)]:
    directory = ROOT / name; directory.mkdir()
    port = FinitePort(); broker = module.ModelBroker(port, utf8_byte_token_bound, config, directory/'calls.json')
    journal = rpc.Journal(directory/'journal')
    payload = {'request': last['request'], 'timeout_seconds': config['timeout_seconds']}
    request = ModelRequest(**{**last['request'], 'messages': tuple(last['request']['messages'])})
    quote = quote_request(port, request, utf8_byte_token_bound)
    assert quote.input_tokens == last['prompt_tokens_reserved'] == 211465
    assert quote.total_tokens == 227849
    assert context_fits(quote, config['max_context_tokens'], config['model_context_tokens'])
    code = 'DONE'
    try:
        journal.execute('original-fallback-replay', {'op':'model.complete','payload':payload}, lambda:broker.complete(payload))
    except rpc.ProtocolError as exc: code = str(exc)
    results.append(dict(guard=name, input_tokens=quote.input_tokens, reserved_output_tokens=quote.output_tokens,
        total_tokens=quote.total_tokens, configured_input_cap=config['max_context_tokens'],
        configured_total_cap=config['model_context_tokens'], rpc_outcome=code, finite_dispatches=port.dispatches))
assert results[0]['rpc_outcome'] == 'OUTCOME_UNKNOWN' and results[0]['finite_dispatches'] == 0
assert results[1]['rpc_outcome'] == 'DONE' and results[1]['finite_dispatches'] == 1

# The five already-paid requests need no extra model/HTTP replay. Assert the
# correction does not change their declared admission or immutable bytes.
prefix = []
for item in calls[:-1]:
    request = ModelRequest(**{**item['request'], 'messages': tuple(item['request']['messages'])})
    quote = quote_request(FinitePort(), request, utf8_byte_token_bound)
    assert quote.input_tokens == item['prompt_tokens_reserved']
    assert quote.total_tokens <= config['max_context_tokens']
    assert context_fits(quote, config['max_context_tokens'], config['model_context_tokens'])
    prefix.append(dict(prompt_version=request.prompt_version, request_digest=item['request_digest'],
        input_tokens=quote.input_tokens, total_tokens=quote.total_tokens, admission_unchanged=True))
assert before == sha(ledger)
assert all(sha(p) == h for p, h in original_hashes.items())
assert len(verified_records(panel_path, verify_panel(panel_path))) == len(rows) == 9
result = dict(schema_version='ce.context_guard_real_request_replay.v1', status='PASS_ZERO_HTTP',
    method_source_sha256=panel['variants']['candidate']['source_sha256'], method_configuration_sha256=panel['variants']['candidate']['config_sha256'],
    recorded_guard_sha256=sha(OLD/'scripts/run_isolated_method.py'), corrected_guard_sha256=sha(SYSTEM/'scripts/run_isolated_method.py'),
    results=results, paid_prefix_unchanged=prefix, original_proofs=original_hashes,
    parent_actual_model_calls=5, child_attempt_records=6, last_paid_dispatches=0,
    original_panel_closed_records=9, original_panel_status='AUDIT_REQUIRED_PRESERVED',
    original_journal_status='PENDING_PRESERVED', financial_ledger_unchanged=True, ledger_sha256=before,
    new_model_http_calls=0, original_receipt_rewritten=False, counts_in_46_slot_panel=False,
    formal_ready=False, scope='Exact saved public request and actual RPC journal path with finite local ports; not model repair success, a retry of the original attempt, or permission to drop the original infrastructure stop')
with (OUT/'ACK_CONTEXT_BOUNDARY_REPLAY.json').open('x') as f: json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'status':result['status'],'old_rpc':results[0]['rpc_outcome'],'new_rpc':results[1]['rpc_outcome'],'new_model_http_calls':0}))
