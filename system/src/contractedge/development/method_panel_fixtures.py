"""Six finite, public-only fixtures for the D2 production-mode panel.

These are published developer fixtures, not benchmark results.  The host runner
admits synthetic providers only and provides no OS filesystem/network isolation.
Candidate code receives request configurations, never the expected predicates.
The independent observer assigns the requirement-level verdicts.  Every mode's
literal edits below is authored directly from these public requirements.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Mapping

from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.benchmark import file_sha
from contractedge.development.public_demo import PublicExampleRunner
from contractedge.orchestration.repair import write_json
from contractedge.source.snapshot import compute_snapshot


MODES = ('full', 'without_relation', 'without_changeset', 'direct')
PROBE_ID = 'contractedge-d2-requirement-panel-v1'
SCOPE = 'Finite published process outputs; no hidden or internal-state claim.'
SCENARIO = 'public-requirement-panel'
BOUNDARY = ('Host synthetic-only developer panel. Separate Python candidate and '
            'observer processes; no OS filesystem or network sandbox.')


def _step(identifier, config, expected):
    return {'id': identifier, 'config': config, 'expected': expected}


def _check(identifier, statement, assertions, *, preserve=False):
    return {'id': identifier, 'statement': statement, 'assertions': assertions,
            'kind': 'PRESERVATION' if preserve else 'REPAIR', 'scope': SCOPE}


def _at(scenario, step, **fields):
    return {'scenario_id': scenario, 'step_id': step, 'expected': fields}


# Each source is independently declared and frozen, including the base variants.
# Exception handling belongs to the observation bridge, not these applications.
CASES = {
    'config-binding/base': {
        'public_case_id': 'd2-config-binding-base',
        'title': 'Enabled configuration binding',
        'source': '''def render(config, state=None):
    if config["enabled"]:
        name = "default"
    else:
        name = config["default_name"]
    return {"name": name, "mode": "stable", "state": state}
''',
        'base_config': {'enabled': True, 'selected_name': 'selected-A', 'default_name': 'fallback'},
        'requirement': ('When enabled, render must return selected_name as name. When disabled, '
                        'it must return default_name. Every call must retain mode="stable".'),
        'scenarios': [
            {'id': 'selected', 'steps': [_step('read', {'enabled': True}, {'name': 'selected-A'})]},
            {'id': 'disabled', 'steps': [_step('read', {'enabled': False}, {'name': 'fallback'})]},
        ],
        'checks': [
            _check('selected', 'Enabled output equals selected_name.', [_at('selected', 'read', name='selected-A')]),
            _check('disabled', 'Disabled output retains default_name.', [_at('disabled', 'read', name='fallback')], preserve=True),
            _check('stable', 'Every public call returns stable mode without exception.', 'stable', preserve=True),
        ],
        'broken_profile': {'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'},
    },
    'config-binding/exception-guard': {
        'public_case_id': 'd2-config-binding-exception-guard',
        'title': 'Binding with a guarded exceptional lookup',
        'source': '''def selected_context(config):
    if config["maintenance"]:
        raise LookupError("Selected context unavailable during maintenance")
    return config["selected_name"]


def render(config, state=None):
    if config["enabled"] and not config["maintenance"]:
        name = "default"  # selected context is not consumed
    else:
        name = config["default_name"]
    return {"name": name, "mode": "stable", "state": state}
''',
        'base_config': {'enabled': True, 'maintenance': False, 'selected_name': 'selected-A', 'default_name': 'fallback'},
        'requirement': ('Render has four public enabled/maintenance truth cells. Only True/False '
                        'may resolve the selected context and must return selected_name. False/False, '
                        'False/True and True/True must return default_name. Resolving selected '
                        'context during maintenance raises LookupError, so preserve the enabled '
                        'and not maintenance guard. All four calls complete without exception and '
                        'return mode="stable".'),
        'scenarios': [
            {'id': 'false-false', 'steps': [_step('read', {'enabled': False, 'maintenance': False}, {'name': 'fallback'})]},
            {'id': 'false-true', 'steps': [_step('read', {'enabled': False, 'maintenance': True}, {'name': 'fallback'})]},
            {'id': 'true-false', 'steps': [_step('read', {'enabled': True, 'maintenance': False}, {'name': 'selected-A'})]},
            {'id': 'true-true', 'steps': [_step('read', {'enabled': True, 'maintenance': True}, {'name': 'fallback'})]},
        ],
        'checks': [
            _check('selected', 'Enabled and non-maintenance resolves and returns selected_name.', [_at('true-false', 'read', name='selected-A')]),
            _check('guard-false-false', 'False/False returns default_name without exception.', [_at('false-false', 'read', name='fallback')], preserve=True),
            _check('guard-false-true', 'False/True returns default_name without exception.', [_at('false-true', 'read', name='fallback')], preserve=True),
            _check('guard-true-true', 'Enabled maintenance returns default_name without exception.', [_at('true-true', 'read', name='fallback')], preserve=True),
            _check('stable', 'Every truth cell completes with stable mode.', 'stable', preserve=True),
        ],
        'broken_profile': {'selected': 'FAIL', 'guard-false-false': 'PASS', 'guard-false-true': 'PASS', 'guard-true-true': 'PASS', 'stable': 'PASS'},
    },
    'two-consumers/base': {
        'public_case_id': 'd2-two-consumers-base',
        'title': 'Two independent production consumers',
        'source': '''def render(config, state=None):
    if config["enabled"]:
        primary = "default"
        secondary = "default"
    else:
        primary = secondary = config["default_name"]
    return {"primary": primary, "secondary": secondary, "mode": "stable", "state": state}
''',
        'base_config': {'enabled': True, 'selected_name': 'selected-A', 'default_name': 'fallback'},
        'requirement': ('When enabled, both primary and secondary production outputs must equal '
                        'selected_name. Disabled calls must retain default_name for both outputs. '
                        'Every call must retain mode="stable".'),
        'scenarios': [
            {'id': 'selected', 'steps': [_step('read', {'enabled': True}, {'primary': 'selected-A', 'secondary': 'selected-A'})]},
            {'id': 'disabled', 'steps': [_step('read', {'enabled': False}, {'primary': 'fallback', 'secondary': 'fallback'})]},
        ],
        'checks': [
            _check('primary', 'Enabled primary output equals selected_name.', [_at('selected', 'read', primary='selected-A')]),
            _check('secondary', 'Enabled secondary output equals selected_name.', [_at('selected', 'read', secondary='selected-A')]),
            _check('disabled', 'Disabled consumers retain default_name.', [_at('disabled', 'read', primary='fallback', secondary='fallback')], preserve=True),
            _check('stable', 'Every public call returns stable mode.', 'stable', preserve=True),
        ],
        'broken_profile': {'primary': 'FAIL', 'secondary': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'},
    },
    'two-consumers/preserve-one': {
        'public_case_id': 'd2-two-consumers-preserve-one',
        'title': 'Repair secondary while preserving an initially correct primary',
        'source': '''def render(config, state=None):
    if config["enabled"]:
        primary = config["selected_name"]  # public S0 already satisfies this consumer
        secondary = "default"
    else:
        primary = secondary = config["default_name"]
    return {"primary": primary, "secondary": secondary, "mode": "stable", "state": state}
''',
        'base_config': {'enabled': True, 'selected_name': 'selected-A', 'default_name': 'fallback'},
        'requirement': ('Enabled primary is already correct in the published starting program and '
                        'must remain selected_name. Repair secondary to selected_name as well. '
                        'Disabled calls retain default_name in both consumers. All calls retain '
                        'mode="stable".'),
        'scenarios': [
            {'id': 'selected', 'steps': [_step('read', {'enabled': True}, {'primary': 'selected-A', 'secondary': 'selected-A'})]},
            {'id': 'disabled', 'steps': [_step('read', {'enabled': False}, {'primary': 'fallback', 'secondary': 'fallback'})]},
        ],
        'checks': [
            _check('primary', 'Preserve the initially correct enabled primary consumer.', [_at('selected', 'read', primary='selected-A')], preserve=True),
            _check('secondary', 'Enabled secondary output equals selected_name.', [_at('selected', 'read', secondary='selected-A')]),
            _check('disabled', 'Disabled consumers retain default_name.', [_at('disabled', 'read', primary='fallback', secondary='fallback')], preserve=True),
            _check('stable', 'Every public call returns stable mode.', 'stable', preserve=True),
        ],
        'broken_profile': {'primary': 'PASS', 'secondary': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'},
    },
    'reload-state/base': {
        'public_case_id': 'd2-reload-state-base',
        'title': 'Refresh an already warmed state',
        'source': '''def render(config, state=None):
    if state is None:
        state = {"name": config["selected_name"]}
    else:
        pass  # configuration reload leaves cached name stale
    name = state["name"] if config["enabled"] else config["default_name"]
    return {"name": name, "mode": "stable", "state": state}
''',
        'base_config': {'enabled': True, 'selected_name': 'A', 'default_name': 'fallback'},
        'requirement': ('Warm state using selected_name=A. A later enabled call with selected_name=B '
                        'must refresh the public output to B. Disabled output remains default_name. '
                        'Every call, including warm-up, must retain mode="stable".'),
        'scenarios': [
            {'id': 'reload', 'steps': [_step('warm-A', {'selected_name': 'A'}, {'name': 'A'}), _step('read-B', {'selected_name': 'B'}, {'name': 'B'})]},
            {'id': 'disabled', 'steps': [_step('read', {'enabled': False}, {'name': 'fallback'})]},
        ],
        'checks': [
            _check('warm-A', 'The deterministic initial warm output is A.', [_at('reload', 'warm-A', name='A')], preserve=True),
            _check('reload-B', 'A subsequent enabled configuration refreshes output to B.', [_at('reload', 'read-B', name='B')]),
            _check('disabled', 'Disabled output retains default_name.', [_at('disabled', 'read', name='fallback')], preserve=True),
            _check('stable', 'Every public call retains stable mode.', 'stable', preserve=True),
        ],
        'broken_profile': {'warm-A': 'PASS', 'reload-B': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'},
    },
    'reload-state/rejected-reload': {
        'public_case_id': 'd2-reload-state-rejected-reload',
        'title': 'Preserve A on rejection and publish B on a valid reload',
        'source': '''def render(config, state=None):
    if state is None:
        state = {"name": config["selected_name"]}
    accepted = True
    if config.get("reload", False):
        value = config["selected_name"]
        if not isinstance(value, str) or not value.strip():
            accepted = False
        else:
            pass  # valid reload is accepted but the previous value remains
    return {"name": state["name"], "accepted": accepted, "mode": "stable", "state": state}
''',
        'base_config': {'selected_name': 'A', 'reload': False},
        'requirement': ('Start deterministically with public state A. Reloading selected_name=None '
                        'must report accepted=False and continue to read A; a following read must '
                        'also return A. A later valid reload to B must report accepted=True and '
                        'read B; a following read must still return B. All five calls complete '
                        'without exception and retain mode="stable".'),
        'scenarios': [
            {'id': 'sequence', 'steps': [
                _step('initial-A', {'selected_name': 'A'}, {'name': 'A', 'accepted': True}),
                _step('reject-invalid', {'selected_name': None, 'reload': True}, {'name': 'A', 'accepted': False}),
                _step('read-after-reject', {'selected_name': 'ignored'}, {'name': 'A', 'accepted': True}),
                _step('accept-B', {'selected_name': 'B', 'reload': True}, {'name': 'B', 'accepted': True}),
                _step('read-after-B', {'selected_name': 'ignored'}, {'name': 'B', 'accepted': True}),
            ]},
        ],
        'checks': [
            _check('initial-A', 'The deterministic starting output is A.', [_at('sequence', 'initial-A', name='A', accepted=True)], preserve=True),
            _check('reject-invalid', 'Invalid reload rejects and reads A.', [_at('sequence', 'reject-invalid', name='A', accepted=False)], preserve=True),
            _check('read-after-reject', 'Read after rejected reload retains A.', [_at('sequence', 'read-after-reject', name='A')], preserve=True),
            _check('accept-B', 'Valid reload accepts and reads B.', [_at('sequence', 'accept-B', name='B', accepted=True)]),
            _check('read-after-B', 'Subsequent read retains B.', [_at('sequence', 'read-after-B', name='B')]),
            _check('stable', 'Every public call retains stable mode without exception.', 'stable', preserve=True),
        ],
        'broken_profile': {'initial-A': 'PASS', 'reject-invalid': 'PASS', 'read-after-reject': 'PASS', 'accept-B': 'FAIL', 'read-after-B': 'FAIL', 'stable': 'PASS'},
    },
}

for _key, _value in CASES.items():
    _value['fixture_id'] = _key
    _value['preservation_item_ids'] = [c['id'] for c in _value['checks'] if c['kind'] == 'PRESERVATION']
    _value['repair_item_ids'] = [c['id'] for c in _value['checks'] if c['kind'] == 'REPAIR']


def case_definition(case_id):
    """Resolve either the human fixture key or its manifest-safe public case ID."""
    if case_id in CASES:
        return copy.deepcopy(CASES[case_id])
    matches = [value for value in CASES.values() if value['public_case_id'] == case_id]
    if len(matches) != 1:
        raise ValueError('Unknown D2 public fixture: ' + str(case_id))
    return copy.deepcopy(matches[0])


def _edit(old, new):
    return [{'change_id': 'C1', 'path': 'app.py', 'operation': 'MODIFY',
             'old_text': old, 'new_text': new}]


def synthetic_edits(case_id, mode, attempt=1):
    """Independent literal-edit entry points; no mode derives a Full response.

    The fixture provider intentionally uses equal successful repairs across
    modes.  The panel compares actual paths/artifacts, not manufactured scores.
    Repeating a successful patch against an already repaired tree is an error.
    """
    case = case_definition(case_id)['fixture_id']
    if mode not in MODES or not isinstance(attempt, int) or attempt < 1:
        raise ValueError('Unsupported mode or attempt')
    if mode == 'full':
        pairs = {
            'config-binding/base': ('name = "default"', 'name = config["selected_name"]'),
            'config-binding/exception-guard': ('name = "default"  # selected context is not consumed', 'name = selected_context(config)'),
            'two-consumers/base': ('primary = "default"\n        secondary = "default"', 'primary = config["selected_name"]\n        secondary = config["selected_name"]'),
            'two-consumers/preserve-one': ('secondary = "default"', 'secondary = config["selected_name"]'),
            'reload-state/base': ('pass  # configuration reload leaves cached name stale', 'state["name"] = config["selected_name"]'),
            'reload-state/rejected-reload': ('pass  # valid reload is accepted but the previous value remains', 'state["name"] = value'),
        }
        return _edit(*pairs[case])
    if mode == 'without_relation':
        # Independently authored direct requirement-to-edit literals.
        pairs = {
            'config-binding/base': ('name = "default"', 'name = config["selected_name"]'),
            'config-binding/exception-guard': ('name = "default"  # selected context is not consumed', 'name = selected_context(config)'),
            'two-consumers/base': ('primary = "default"\n        secondary = "default"', 'primary = config["selected_name"]\n        secondary = config["selected_name"]'),
            'two-consumers/preserve-one': ('secondary = "default"', 'secondary = config["selected_name"]'),
            'reload-state/base': ('pass  # configuration reload leaves cached name stale', 'state["name"] = config["selected_name"]'),
            'reload-state/rejected-reload': ('pass  # valid reload is accepted but the previous value remains', 'state["name"] = value'),
        }
        return _edit(*pairs[case])
    if mode == 'without_changeset':
        # Independently authored diagnosis-to-source literals, not plan stripping.
        pairs = {
            'config-binding/base': ('name = "default"', 'name = config["selected_name"]'),
            'config-binding/exception-guard': ('name = "default"  # selected context is not consumed', 'name = selected_context(config)'),
            'two-consumers/base': ('primary = "default"\n        secondary = "default"', 'primary = config["selected_name"]\n        secondary = config["selected_name"]'),
            'two-consumers/preserve-one': ('secondary = "default"', 'secondary = config["selected_name"]'),
            'reload-state/base': ('pass  # configuration reload leaves cached name stale', 'state["name"] = config["selected_name"]'),
            'reload-state/rejected-reload': ('pass  # valid reload is accepted but the previous value remains', 'state["name"] = value'),
        }
        return _edit(*pairs[case])
    # The direct path consumes only the raw public requirement/source requests.
    pairs = {
        'config-binding/base': ('name = "default"', 'name = config["selected_name"]'),
        'config-binding/exception-guard': ('name = "default"  # selected context is not consumed', 'name = selected_context(config)'),
        'two-consumers/base': ('primary = "default"\n        secondary = "default"', 'primary = config["selected_name"]\n        secondary = config["selected_name"]'),
        'two-consumers/preserve-one': ('secondary = "default"', 'secondary = config["selected_name"]'),
        'reload-state/base': ('pass  # configuration reload leaves cached name stale', 'state["name"] = config["selected_name"]'),
        'reload-state/rejected-reload': ('pass  # valid reload is accepted but the previous value remains', 'state["name"] = value'),
    }
    return _edit(*pairs[case])


# These scripts also run inside separate Docker processes without importing this
# package. Paths are arguments so the parent can choose host or mounted paths.
CANDIDATE = r'''import importlib.util,json,pathlib,sys
source=pathlib.Path(sys.argv[1]); requests=json.loads(pathlib.Path(sys.argv[2]).read_text())
spec=importlib.util.spec_from_file_location('d2_panel_candidate',source/'app.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
results=[]
for scenario in requests['scenarios']:
    state=None
    for step in scenario['steps']:
        try:
            actual=module.render(step['config'],state)
            if not isinstance(actual,dict):raise TypeError('render must return an object')
            state=actual.get('state'); actual={k:v for k,v in actual.items() if k!='state'}
            actual=json.loads(json.dumps(actual))
        except Exception as exc:
            actual={'exception':{'type':type(exc).__name__,'message':str(exc)}}
        results.append({'scenario_id':scenario['id'],'step_id':step['id'],'actual':actual})
print(json.dumps({'schema_version':'ce.d2_panel_outputs.v1','results':results},sort_keys=True))
'''

OBSERVER = r'''import json,pathlib,sys
def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result
spec=json.loads(pathlib.Path(sys.argv[1]).read_text(),object_pairs_hook=unique)
body=json.loads(pathlib.Path(sys.argv[2]).read_text(),object_pairs_hook=unique)
if not isinstance(body,dict) or set(body)!={'schema_version','results'} or body['schema_version']!='ce.d2_panel_outputs.v1':
    raise ValueError('Candidate must return actual process values')
expected_keys=[(s['id'],step['id']) for s in spec['scenarios'] for step in s['steps']]
rows=body['results']; by_id={}
if not isinstance(rows,list) or len(rows)!=len(expected_keys):raise ValueError('Wrong result count')
for row in rows:
    if not isinstance(row,dict) or set(row)!={'scenario_id','step_id','actual'} or not isinstance(row['actual'],dict):raise ValueError('Invalid result')
    key=(row['scenario_id'],row['step_id'])
    if key in by_id:raise ValueError('Duplicate result')
    by_id[key]=row['actual']
if set(by_id)!=set(expected_keys):raise ValueError('Wrong result identities')
checks=[]
for check in spec['checks']:
    actual=[]; expected=[]; passed=True
    if check['assertions']=='stable':
        for key in expected_keys:
            value=by_id[key]; actual.append({'scenario_id':key[0],'step_id':key[1],'mode':value.get('mode'),'exception':value.get('exception')})
            expected.append({'scenario_id':key[0],'step_id':key[1],'mode':'stable','exception':None})
            passed=passed and value.get('mode')=='stable' and 'exception' not in value
    else:
        for assertion in check['assertions']:
            key=(assertion['scenario_id'],assertion['step_id']); value=by_id[key]
            selected={k:value.get(k) for k in assertion['expected']}
            actual.append({'scenario_id':key[0],'step_id':key[1],'fields':selected,'exception':value.get('exception')})
            expected.append({'scenario_id':key[0],'step_id':key[1],'fields':assertion['expected'],'exception':None})
            passed=passed and selected==assertion['expected'] and 'exception' not in value
    checks.append({'id':check['id'],'statement':check['statement'],'kind':check['kind'],'scope':check['scope'],
        'status':'PASS' if passed else 'FAIL','actual':actual,'expected':expected})
print(json.dumps({'schema_version':'ce.d2_requirement_checks.v1','fixture_id':spec['fixture_id'],
    'snapshot_id':spec['snapshot_id'],'input_digest':spec['input_digest'],'spec_digest':spec['spec_digest'],
    'checks':checks},sort_keys=True))
'''


def runtime_inputs(case_id):
    case = case_definition(case_id)
    return {'schema_version': 'ce.d2_panel_public_inputs.v1', 'fixture_id': case['fixture_id'],
            'base_config': case['base_config'], 'scenarios': case['scenarios'], 'checks': case['checks']}


def candidate_inputs(case_id):
    """Project only configurations; expected output assertions are withheld."""
    case = case_definition(case_id)
    return {'scenarios': [{'id': scenario['id'], 'steps': [
        {'id': step['id'], 'config': {**case['base_config'], **step['config']}}
        for step in scenario['steps']]} for scenario in case['scenarios']]}


def _public_spec(case_id):
    case = case_definition(case_id)
    return {'fixture_id': case['fixture_id'], 'scenarios': case['scenarios'], 'checks': case['checks']}


def check_capabilities(case_id):
    case = case_definition(case_id)
    return {'schema_version': 'ce.public_process_check_capabilities.v1', 'probe_id': PROBE_ID,
            'observer_sha256': hashlib.sha256(OBSERVER.encode()).hexdigest(),
            'checks': [{'id': PROBE_ID + ':' + c['id'], 'statement': c['statement'], 'scope': SCOPE,
                        'scenario': SCENARIO, 'scenario_members': _scenario_members(case, c)}
                       for c in case['checks']]}


def _scenario_members(case, check):
    if check['assertions'] == 'stable':
        return [s['id'] for s in case['scenarios']]
    return list(dict.fromkeys(a['scenario_id'] for a in check['assertions']))


def freeze_panel_case(case_id, output, *, runtime='host'):
    """Emit public_demo's frozen task/archive protocol for one of six fixtures."""
    case = case_definition(case_id)
    if runtime not in {'host', 'isolated'}:
        raise ValueError('Unsupported six-case public runtime')
    frozen = Path(output) / 'frozen-public'
    task = frozen / 'public/tasks' / case['public_case_id']
    task.mkdir(parents=True, exist_ok=False)
    (task / 'requirement.md').write_text(case['requirement'] + '\n', encoding='utf-8')
    write_json(task / 'runtime-inputs.json', runtime_inputs(case_id))
    with (task / 'source.tar.gz').open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as zipped:
            with tarfile.open(fileobj=zipped, mode='w') as archive:
                data = {'app.py': case['source'].encode(),
                        'config.yaml': (json.dumps(case['base_config'], sort_keys=True) + '\n').encode()}
                for name, content in sorted(data.items()):
                    member = tarfile.TarInfo('repo/' + name)
                    member.size, member.mode = len(content), 0o644
                    archive.addfile(member, io.BytesIO(content))
    (task / 'task.yaml').write_text('schema_version: contractedge.method_task.v1\nrequirement_file: requirement.md\nsource_archive: source.tar.gz\nexecution_config: environment.json\n', encoding='utf-8')
    write_json(task / 'environment.json', {'editable_paths': ['app.py'],
        'network': 'none' if runtime == 'isolated' else 'host-process-not-enforced', 'public_scope': 'SYNTHETIC_DEVELOPER_PANEL_ONLY',
        'observation_boundary': ('RuntimeSession candidate plus independent six-case observer; actual isolation/limits/cleanup must be verified per observation.' if runtime == 'isolated' else BOUNDARY), 'registered_public_checks': check_capabilities(case_id),
        'scenarios': [{'name': 'public-requirement-panel', 'argv': ['python', 'published-panel-observer']}]})
    names = sorted(p.name for p in task.iterdir())
    manifest = {'case_id': case['public_case_id'], 'source_sha256': file_sha(task / 'source.tar.gz'),
                'files': names, 'file_sha256': {name: file_sha(task / name) for name in names}}
    write_json(task / 'manifest.json', manifest)
    row = {'case_id': case['public_case_id'], 'primary_class': 'R1', 'task_path': 'tasks/' + case['public_case_id'],
           'source_sha256': manifest['source_sha256'], 'task_manifest_sha256': file_sha(task / 'manifest.json')}
    (frozen / 'public/manifest.jsonl').write_text(json.dumps(row, sort_keys=True) + '\n', encoding='utf-8')
    write_json(frozen / 'RELEASE_ENTRY.json', {'status': 'FROZEN_LOCAL_SUPPORTED_PLATFORM_RELEASE',
        'version': 'd2-public-developer-panel-v1', 'selected_cases': 1, 'by_class': {'R1': 1},
        'public_manifest_sha256': file_sha(frozen / 'public/manifest.jsonl')})
    return frozen / 'public'


def _record_body(check, members):
    return {'probe_id': PROBE_ID, 'id': check['id'], 'status': check['status'],
            'scenario': SCENARIO, 'scenario_members': members,
            'statement': check['statement'], 'observed': {
                'actual': check['actual'], 'expected': check['expected'], 'scope': SCOPE}}


def requirement_observation(case_id, snapshot_id, input_digest, checks, *, locator_path):
    """Bind separate observer output to a snapshot, frozen input and exact records."""
    case = case_definition(case_id)
    observations = []
    observer_sha = hashlib.sha256(OBSERVER.encode()).hexdigest()
    definitions = {c['id']: c for c in case['checks']}
    for check in checks:
        members = _scenario_members(case, definitions[check['id']])
        excerpt = json.dumps(_record_body(check, members), sort_keys=True)
        observations.append({'id': 'OBS-' + check['id'], 'snapshot_id': snapshot_id,
            'scenario': SCENARIO,
            'kind': 'PUBLIC_OBSERVATION', 'evidence_kind': 'PUBLIC_BEHAVIOR',
            'locator': {'path': str(locator_path), 'field': check['id'], 'truncated': False, 'whole_file_read': True},
            'content_digest': hashlib.sha256(excerpt.encode()).hexdigest(), 'excerpt': excerpt,
            'origin': 'Independent published D2 requirement observer; host synthetic only',
            'structured_complete': True,
            'behavior_check': {'id': PROBE_ID + ':' + check['id'], 'status': check['status'], 'probe_sha256': observer_sha}})
    return {'status': 'EXECUTION_PASS', 'snapshot_id': snapshot_id, 'input_digest': input_digest,
        'observations': observations, 'environment_digest': canonical_digest({'python': sys.version, 'observer': OBSERVER}),
        'requirement_profile': {'schema_version': 'ce.d2_requirement_profile.v1', 'fixture_id': case['fixture_id'],
            'snapshot_id': snapshot_id, 'input_digest': input_digest, 'spec_digest': canonical_digest(_public_spec(case_id)),
            'observations_digest': canonical_digest(observations), 'observer_sha256': observer_sha,
            'check_ids': sorted(c['id'] for c in case['checks']), 'checks': checks, 'status': 'COMPLETE',
            'preservation_item_ids': case['preservation_item_ids'], 'scope': SCOPE},
        'boundary': BOUNDARY, 'synthetic_only': True, 'isolated': False}


def enforce_requirement_profile(review, observation):
    """Fixed public requirements cannot be omitted by model plan/review output.

    Invalid or stale profiles fail closed to UNKNOWN.  Requirement checks remain
    separate from semantic change/postcondition IDs and never fabricate C/P rows.
    """
    profile = observation.get('requirement_profile')
    valid = isinstance(profile, Mapping)
    case = None
    try:
        case = case_definition(profile['fixture_id']) if valid else None
    except (KeyError, ValueError, TypeError):
        valid = False
    checks = profile.get('checks') if isinstance(profile, Mapping) else None
    observer_sha = hashlib.sha256(OBSERVER.encode()).hexdigest()
    if valid:
        valid = (observation.get('status') == 'EXECUTION_PASS'
            and profile.get('schema_version') == 'ce.d2_requirement_profile.v1'
            and profile.get('status') == 'COMPLETE'
            and isinstance(observation.get('snapshot_id'), str) and bool(observation['snapshot_id'])
            and profile.get('snapshot_id') == observation.get('snapshot_id')
            and profile.get('input_digest') == observation.get('input_digest')
            and isinstance(profile.get('input_digest'), str) and len(profile['input_digest']) == 64
            and profile.get('spec_digest') == canonical_digest(_public_spec(case['fixture_id']))
            and profile.get('observer_sha256') == observer_sha
            and profile.get('scope') == SCOPE
            and profile.get('preservation_item_ids') == case['preservation_item_ids']
            and profile.get('check_ids') == sorted(c['id'] for c in case['checks'])
            and isinstance(checks, list) and len(checks) == len(case['checks'])
            and all(isinstance(c, Mapping) and c.get('status') in {'PASS', 'FAIL'} for c in checks)
            and [c.get('id') for c in checks] == [c['id'] for c in case['checks']]
            and profile.get('observations_digest') == canonical_digest(observation.get('observations', [])))
    if valid:
        records = observation.get('observations')
        valid = (isinstance(records, list) and len(records) == len(checks)
                 and all(isinstance(r, Mapping) for r in records)
                 and [r.get('id') for r in records] == ['OBS-' + c['id'] for c in checks])
        for definition, check, record in zip(case['checks'], checks, records if valid else []):
            members = _scenario_members(case, definition)
            excerpt = record.get('excerpt')
            try:
                parsed = json.loads(excerpt)
            except (TypeError, ValueError):
                valid = False
                break
            locator = record.get('locator')
            valid = (check.get('statement') == definition['statement'] and check.get('kind') == definition['kind']
                and check.get('scope') == SCOPE and isinstance(check.get('actual'), list)
                and isinstance(check.get('expected'), list) and parsed == _record_body(check, members)
                and record.get('scenario') == SCENARIO
                and record.get('snapshot_id') == observation['snapshot_id']
                and record.get('evidence_kind') == 'PUBLIC_BEHAVIOR' and record.get('structured_complete') is True
                and hashlib.sha256(excerpt.encode()).hexdigest() == record.get('content_digest')
                and isinstance(locator, Mapping) and locator.get('truncated') is False
                and locator.get('whole_file_read') is True
                and record.get('behavior_check') == {'id': PROBE_ID + ':' + check['id'], 'status': check['status'], 'probe_sha256': observer_sha})
            # Recompute predicates from the observed projections and the immutable
            # published spec, so changing a status plus digest cannot flip a FAIL.
            expected = []
            if definition['assertions'] == 'stable':
                expected = [{'scenario_id': s['id'], 'step_id': step['id'], 'mode': 'stable', 'exception': None}
                            for s in case['scenarios'] for step in s['steps']]
            else:
                expected = [{'scenario_id': a['scenario_id'], 'step_id': a['step_id'], 'fields': a['expected'], 'exception': None}
                            for a in definition['assertions']]
            valid = valid and check.get('expected') == expected and check.get('status') == ('PASS' if check.get('actual') == expected else 'FAIL')
            if not valid:
                break
    decision = ('FAIL' if any(c['status'] == 'FAIL' for c in checks) else 'PASS') if valid else 'UNKNOWN'
    final = 'FAIL' if decision == 'FAIL' else 'UNKNOWN' if decision == 'UNKNOWN' else review.get('decision', 'UNKNOWN')
    fixed_checks = [{'id': check['id'], 'status': check['status'],
                     'rationale': 'Independent fixed public requirement predicate: ' + check['statement'],
                     'evidence': [{'observation_id': 'OBS-' + check['id'],
                                   'quote': record['excerpt']}]}
                    for check, record in zip(checks, records)] if valid else []
    return {**review, 'decision': final,
        'public_requirement_checks': fixed_checks,
        'requirement_profile_valid': bool(valid), 'requirement_profile_decision': decision,
        'requirement_profile_validation': {'binding_valid': bool(valid), 'decision': decision,
            'profile_digest': canonical_digest(profile), 'scope': SCOPE},
        'rationale': str(review.get('rationale', '')) + ' Fixed published D2 requirements remain mandatory.'}


class PanelRunner(PublicExampleRunner):
    """Separate host processes for a zero-cost synthetic-only developer panel."""
    synthetic_only = True
    requires_public_profile = False
    requires_requirement_profile = True
    runtime_kind = 'host-synthetic-d2-panel'

    def __init__(self, public, case):
        definition = case_definition(case)
        super().__init__(public, definition['public_case_id'])
        self.fixture_id = definition['fixture_id']

    def enforce_requirement_profile(self, review, observation):
        return enforce_requirement_profile(review, observation)

    def observe(self, workspace, output):
        workspace, output = Path(workspace), Path(output)
        output.mkdir(parents=True, exist_ok=False)
        record = json.loads((workspace / 'workspace.json').read_text(encoding='utf-8'))
        source = Path(record['source_root'])
        snapshot = compute_snapshot(source).snapshot_id
        inputs_path = workspace / 'inputs/runtime-inputs.json'
        frozen_inputs = json.loads(inputs_path.read_text(encoding='utf-8'))
        if frozen_inputs != runtime_inputs(self.fixture_id):
            raise ValueError('Published panel inputs differ from the fixed fixture specification')
        input_digest = file_sha(inputs_path)
        requests_path = output / 'candidate-inputs.json'
        checks_path = output / 'checks.json'
        write_json(requests_path, candidate_inputs(self.fixture_id))
        spec = _public_spec(self.fixture_id)
        write_json(checks_path, {**spec, 'snapshot_id': snapshot, 'input_digest': input_digest,
                                'spec_digest': canonical_digest(spec)})
        environment = {key: os.environ[key] for key in ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR'] if key in os.environ}
        environment['LANG'] = 'C.UTF-8'
        argv = [sys.executable, '-I', '-B', '-c', CANDIDATE, str(source), str(requests_path)]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_seconds, cwd=source, env=environment)
        stdout_path = output / 'candidate-stdout.txt'
        stdout_path.write_text(result.stdout, encoding='utf-8')
        (output / 'candidate-stderr.txt').write_text(result.stderr, encoding='utf-8')
        observer_argv = [sys.executable, '-I', '-B', '-c', OBSERVER, str(checks_path), str(stdout_path)]
        observed = subprocess.run(observer_argv, capture_output=True, text=True, timeout=self.timeout_seconds,
                                  cwd=output, env=environment) if result.returncode == 0 else None
        (output / 'stdout.txt').write_text(observed.stdout if observed else '', encoding='utf-8')
        (output / 'stderr.txt').write_text(observed.stderr if observed else '', encoding='utf-8')
        write_json(output / 'process.json', {'candidate_argv': argv, 'candidate_returncode': result.returncode,
            'observer_argv': observer_argv, 'observer_returncode': observed.returncode if observed else None,
            'environment_keys': sorted(environment), 'python_isolated': True, 'boundary': BOUNDARY,
            'synthetic_only': True, 'os_isolation': False})
        if observed is not None and observed.returncode == 0:
            body = json.loads(observed.stdout)
            if (body.get('schema_version') != 'ce.d2_requirement_checks.v1' or body.get('fixture_id') != self.fixture_id
                or body.get('snapshot_id') != snapshot or body.get('input_digest') != input_digest
                or body.get('spec_digest') != canonical_digest(spec)):
                raise ValueError('Independent panel observer identity mismatch')
            report = requirement_observation(self.fixture_id, snapshot, input_digest, body['checks'],
                locator_path=(output / 'stdout.txt').relative_to(workspace.parent))
            if not enforce_requirement_profile({'decision': 'UNKNOWN'}, report)['requirement_profile_validation']['binding_valid']:
                raise ValueError('Independent panel requirement profile failed validation')
        else:
            report = {'status': 'INVALID_OBSERVATION', 'snapshot_id': snapshot, 'input_digest': input_digest,
                      'observations': [], 'errors': ['Candidate or independent public observer did not complete.'],
                      'boundary': BOUNDARY, 'synthetic_only': True, 'isolated': False}
        write_json(output / 'observation.json', report)
        return report
