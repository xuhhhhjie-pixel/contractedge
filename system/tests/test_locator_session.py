"""Read-only locator session (front-half redesign batch 3b), driven by scripted replies."""
import json
from types import SimpleNamespace

from contractedge.retrieval.locator import LocatorCorrections, LocatorLimits, run_locator
from contractedge.source.navigation import SourceNavigator
from contractedge.source.snapshot import compute_snapshot

FILES = {
    'go.mod': 'module example.org/svc\n',
    'pkg/dns/util.go': ('package dns\n' + ''.join(f'func helper{i}() {{}}\n' for i in range(60))
                        + 'func lookupCNAMETarget(fqdn string) (string, error) {\n\treturn "", nil\n}\n'),
    'pkg/other/noise.go': 'package other\nfunc Noise() { lookupCNAMETarget() }\n',
}


def navigator(tmp_path):
    root = tmp_path / 'repo'
    for name, body in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(body.encode())
    snapshot = compute_snapshot(root)
    return SourceNavigator(snapshot), snapshot


class Script:
    """Replies are functions of the conversation so they can use returned handles."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        content = reply(request) if callable(reply) else reply
        return SimpleNamespace(content=content if isinstance(content, str) else json.dumps(content),
                               truncated=getattr(reply, 'truncated', False))


def last_result(request):
    return json.loads(request.messages[-1]['content'])['tool_result']


def handle_of(request, symbol):
    return next(h['handle'] for h in last_result(request)['hits'] if h['symbol'] == symbol)


def run(nav, snapshot, script, **kwargs):
    return run_locator(nav, questions=[{'id': 'L1', 'summary': 'Where is the CNAME lookup?'}],
                       task={'requirement_text': 'Resolve CNAME before TXT'}, complete=script,
                       truncated=lambda r: r.truncated, model='m', current_snapshot=snapshot.snapshot_id, **kwargs)


def test_search_read_finish_delivers_only_selected_exact_bodies(tmp_path):
    nav, snapshot = navigator(tmp_path)
    script = Script(
        {'action': 'search', 'arguments': {'symbol': 'lookupCNAMETarget'}},
        lambda r: {'action': 'read', 'arguments': {'handle': handle_of(r, 'lookupCNAMETarget')}},
        {'action': 'search', 'arguments': {'scope': 'pkg/other', 'symbol': 'Noise'}},
        lambda r: {'action': 'read', 'arguments': {'handle': handle_of(r, 'Noise')}},
        # messages[5] is the result of the first read (lookupCNAMETarget); Noise is not delivered.
        lambda r: {'action': 'finish', 'deliver': [json.loads(r.messages[5]['content'])['tool_result']['handle']],
                   'open_questions': ['caller of the solver']})
    outcome = run(nav, snapshot, script)
    assert outcome.status == 'FINISHED' and len(outcome.delivered_records) == 1
    record = outcome.delivered_records[0]
    assert record['excerpt'].startswith('func lookupCNAMETarget') and record['kind'] == 'SOURCE_SPAN'
    assert len(outcome.record['reads']) == 2 and len(outcome.record['delivered']) == 1   # Noise read, not delivered
    assert outcome.open_questions == ['caller of the solver']
    assert all(r.prompt_version == 'locator.v1' for r in script.requests)
    # Bodies reach the locator only through read results, never through search pages.
    assert 'return "", nil' not in script.requests[1].messages[-1]['content']


def test_unread_handle_cannot_be_delivered(tmp_path):
    nav, snapshot = navigator(tmp_path)
    script = Script(
        {'action': 'search', 'arguments': {'symbol': 'lookupCNAMETarget'}},
        lambda r: {'action': 'finish', 'deliver': [handle_of(r, 'lookupCNAMETarget')]},
        lambda r: {'action': 'read', 'arguments': {'path': 'pkg/dns/util.go', 'symbol': 'lookupCNAMETarget'}},
        lambda r: {'action': 'finish', 'deliver': [last_result(r)['handle']]})
    outcome = run(nav, snapshot, script)
    assert outcome.status == 'FINISHED' and len(outcome.delivered_records) == 1
    assert 'Deliver only handles you read' in script.requests[2].messages[-1]['content']


def test_repeated_queries_are_reused_and_stop_without_progress(tmp_path):
    nav, snapshot = navigator(tmp_path)
    same = {'action': 'search', 'arguments': {'symbol': 'lookupCNAMETarget'}}
    script = Script(same, same, same, same, same)
    outcome = run(nav, snapshot, script, limits=LocatorLimits(no_progress_steps=3))
    assert outcome.status == 'NO_PROGRESS' and outcome.delivered_records == []
    assert len(script.requests) == 4                       # 1 progress + 3 repeats
    assert json.loads(script.requests[2].messages[-1]['content'])['tool_result']['repeated_query'] is True


def test_cut_or_invalid_actions_are_not_executed_and_corrections_are_task_wide(tmp_path):
    nav, snapshot = navigator(tmp_path)
    cut = lambda r: '{"action": "read", "argum'
    cut.truncated = True
    shared = LocatorCorrections(limit=2)
    first = run(nav, snapshot, Script(cut, 'not json', {'action': 'search', 'arguments': {'symbol': 'x'}},
                                      {'action': 'finish'}), corrections=shared)
    assert first.status == 'FINISHED' and shared.used == 2
    assert [s['action'] for s in first.record['steps']][:2] == ['INVALID', 'INVALID']
    second = run(nav, snapshot, Script('still not json'), corrections=shared)
    assert second.status == 'INVALID_OUTPUT'                # no fresh allowance per session


def test_tool_errors_are_returned_as_facts_and_step_limit_holds(tmp_path):
    nav, snapshot = navigator(tmp_path)
    script = Script(*([{'action': 'read', 'arguments': {'path': '../etc/passwd', 'symbol': 'x'}}] +
                      [{'action': 'search', 'arguments': {'text': f'helper{i}('}} for i in range(5)]))
    outcome = run(nav, snapshot, script, limits=LocatorLimits(max_steps=4, no_progress_steps=10))
    assert outcome.status == 'STEP_LIMIT' and len(script.requests) == 4
    assert json.loads(script.requests[1].messages[-1]['content'])['tool_result']['error'] == 'NOT_IN_SNAPSHOT'


def test_follow_up_session_can_reference_prior_deliveries_without_redelivering(tmp_path):
    nav, snapshot = navigator(tmp_path)
    read = nav.read(path='pkg/dns/util.go', symbol='lookupCNAMETarget')
    script = Script({'action': 'finish', 'deliver': [read['handle']]})
    outcome = run(nav, snapshot, script, prior_delivered={read['handle']: read})
    assert outcome.status == 'FINISHED' and outcome.delivered_records == []
    opening = json.loads(script.requests[0].messages[1]['content'])
    assert opening['already_delivered'][0]['handle'] == read['handle']
