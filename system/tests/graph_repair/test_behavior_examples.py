"""Executable examples: expected values from public material, driver calibration, judging and the loop wiring."""
import hashlib
import json
from dataclasses import replace

import pytest

from contractedge.graph_repair import behavior
from contractedge.graph_repair import behavior_examples as ex
from contractedge.graph_repair.behavior_loop import BehaviorLoop
from contractedge.graph_repair.config import VARIANTS, Variant

FIXTURE = ('{"trigger": {"external_name": "vpce-1/subnet-2", "terraform_state": {"id": "a-vpce-1x9"}},\n'
           ' "control": {"external_name": "a-vpce-1y7", "terraform_state": {"id": "a-vpce-1y7"}}}\n')
PATH = '/inputs/inputs/replay-input.json'
REF = [{'source': 'file', 'path': PATH, 'quote': '"trigger": {"external_name": "vpce-1/subnet-2"'}]
CREF = [{'source': 'file', 'path': PATH, 'quote': '"control": {"external_name": "a-vpce-1y7"'}]
PROPOSED = [
    {'name': 'trigger', 'kind': 'target', 'input': 'subnet association', 'source_refs': REF,
     'fields': [{'name': 'terraform_id', 'meaning': 'id from GetIDFn', 'expected': 'a-vpce-1x9'},
                {'name': 'external_name', 'meaning': 'name from state', 'expected': None},
                {'name': 'invented', 'expected': 'not-in-any-source'}]},
    {'name': 'control', 'kind': 'preserve', 'source_refs': CREF,
     'fields': [{'name': 'terraform_id', 'expected': 'a-vpce-1y7'}]},
    {'name': 'nosource', 'kind': 'target', 'source_refs': [{'source': 'task', 'quote': 'quote that is not there'}],
     'fields': [{'name': 'x', 'expected': None}]}]


def sources():
    s = behavior.Sources('Repair the external name.', lambda p: FIXTURE if p == PATH else None)
    s.text(PATH)
    return s


def defined():
    snap = behavior.empty()
    delta = ex.apply_examples(snap, PROPOSED, sources())
    return snap, delta


def test_literal_values_must_occur_in_public_material():
    snap, delta = defined()
    assert delta['added'] == ['trigger', 'control'] and delta['rejected'][0]['item']['name'] == 'nosource'
    fields = snap['examples']['trigger']['fields']
    assert fields['terraform_id'] == {'mode': 'LITERAL', 'expected': 'a-vpce-1x9', 'meaning': 'id from GetIDFn'}
    assert fields['external_name']['mode'] == 'DIFF'
    assert fields['invented']['mode'] == 'DIFF' and 'not found verbatim' in fields['invented']['note']


def obs(**values):
    return {tuple(k.split('__')): v for k, v in values.items()}


def test_calibration_keeps_only_fields_that_can_show_the_fix():
    snap, _ = defined()
    report = ex.calibrate(snap, obs(trigger__terraform_id='vpce-1/subnet-2', trigger__external_name='a-vpce-1x9',
                                    control__terraform_id='a-vpce-1y7'))
    assert {(r['example'], r['field']) for r in report['usable']} == {
        ('trigger', 'terraform_id'), ('trigger', 'external_name'), ('control', 'terraform_id')}
    assert report['dropped'] == [{'example': 'trigger', 'field': 'invented', 'why': 'not observed by the driver'}]
    snap2, _ = defined()
    report = ex.calibrate(snap2, obs(trigger__terraform_id='a-vpce-1x9', trigger__external_name='e',
                                     trigger__invented='i', control__terraform_id='other'))
    why = {(r['example'], r['field']): r['why'] for r in report['dropped']}
    assert 'already equal' in why[('trigger', 'terraform_id')]      # a hard-coded answer is not evidence
    assert 'differs from the expected' in why[('control', 'terraform_id')]


def test_judging_literal_and_differential_fields():
    snap, _ = defined()
    ex.calibrate(snap, obs(trigger__terraform_id='vpce-1/subnet-2', trigger__external_name='a-vpce-1x9',
                           control__terraform_id='a-vpce-1y7'))
    rows = {(r['example'], r['field']): r['status'] for r in ex.judge(snap, obs(
        trigger__terraform_id='vpce-1/subnet-2', trigger__external_name='a-vpce-1x9', control__terraform_id='a-vpce-1y7'))}
    assert rows == {('trigger', 'terraform_id'): 'FAIL', ('trigger', 'external_name'): 'UNCHANGED',
                    ('control', 'terraform_id'): 'PASS'}
    rows = {(r['example'], r['field']): r['status'] for r in ex.judge(snap, obs(
        trigger__terraform_id='a-vpce-1x9', trigger__external_name='vpce-1/subnet-2', control__terraform_id='changed'))}
    assert rows == {('trigger', 'terraform_id'): 'PASS', ('trigger', 'external_name'): 'CHANGED',
                    ('control', 'terraform_id'): 'FAIL'}


def test_observation_lines_and_json_values():
    out = ('noise\nCE_OBSERVE {"example": "trigger", "field": "terraform_id", "value": "a-vpce-1x9"}\n'
           'CE_OBSERVE {"example": "t", "field": "update", "value": true}\nCE_OBSERVE {bad json}\n'
           '    CE_OBSERVE {"example": "indented", "field": "f", "value": 1}\n')
    assert ex.parse(out) == {('trigger', 'terraform_id'): 'a-vpce-1x9', ('t', 'update'): 'true'}


def test_driver_command_runs_in_the_public_runtime_and_removes_its_test_copy():
    command = ex.run_command()
    assert command.startswith('ce-run /bin/sh -c ') and 'GOCACHE=/work/.contractedge-deps/gocache' in command
    assert "trap 'rm -f \"$T\"' EXIT" in command.replace("'\"'\"'", "'") and '-run "^TestCEExampleDriver$"' in command


def test_the_driver_writer_is_not_told_which_value_is_expected():
    snap, _ = defined()
    text = ex.brief(snap, 'Repair the external name.', [])
    assert 'terraform_id (id from GetIDFn)' in text and 'a-vpce-1x9' not in text


def tid(name):
    return hashlib.sha1(name.encode()).hexdigest()


class Fake:
    """Container stand-in: files, a source tree id, and a driver whose output depends on the tree."""

    def __init__(self, driver_writes=True):
        self.files = {PATH: FIXTURE, '/inputs/requirement.md': 'The external name must stay stable.\n'}
        self.tree = 'T0'
        self.commands, self.runs = [], []
        self.driver_writes = driver_writes

    def run(self, command, timeout):
        self.commands.append(command)
        if 'write-tree' in command:
            return {'output': f'TREE={tid(self.tree)}', 'returncode': 0}
        if command.startswith('find /inputs'):
            return {'output': '\n'.join(p for p in self.files if p.startswith('/inputs')), 'returncode': 0}
        if command.startswith('test -f '):
            path = command.split()[2].strip("'")
            return ({'output': self.files[path], 'returncode': 0} if path in self.files else {'output': '', 'returncode': 1})
        if command.startswith('cat >') and self.driver_writes:
            self.files['/testbed/.contractedge-deps/ce-examples/main.go'] = 'package main'
            return {'output': '', 'returncode': 0}
        if command.startswith('sed -i'):
            self.tree = 'T-edited-by-helper'
            return {'output': '', 'returncode': 0}
        if 'git -c safe.directory=* checkout' in command:
            self.tree = 'T0'
            return {'output': '', 'returncode': 0}
        if command.startswith('ce-run'):
            self.runs.append('base' if 'ce-base' in command else self.tree)
            tree = 'T0' if 'ce-base' in command else self.tree
            value = {'T0': 'vpce-1/subnet-2', 'T1': 'vpce-1/subnet-2', 'T2': 'a-vpce-1x9'}.get(tree, '?')
            return {'output': f'CE_OBSERVE {{"example": "trigger", "field": "terraform_id", "value": "{value}"}}\n'
                              'CE_OBSERVE {"example": "control", "field": "terraform_id", "value": "a-vpce-1y7"}\n',
                    'returncode': 0}
        return {'output': '', 'returncode': 0}


class Model:
    """Requirements proposal, then the driver helper's replies."""

    def __init__(self, helper):
        self.helper = list(helper)

    def query(self, messages, **kwargs):
        if messages[0]['content'].startswith('You extract'):
            return {'content': json.dumps({'requirements': [
                {'kind': 'target', 'condition': 'repeated reconciliation', 'expected': 'stable external name',
                 'source_refs': [{'source': 'file', 'path': '/inputs/requirement.md', 'quote': 'external name must stay stable'}]}],
                'examples': PROPOSED[:2]})}
        assert 'expected' not in messages[1]['content'].split('Examples to observe')[1]
        return {'content': 'THOUGHT: step\n\n```bash\n' + self.helper.pop(0) + '\n```'}


HELPER = replace(VARIANTS['behavior-examples'], behavior_driver_helper=True)
DRIVER = '/testbed/.contractedge-deps/ce-examples/main.go'


def loop_for(tmp_path, fake, helper, variant=None):
    return BehaviorLoop(variant or HELPER, model=Model(helper), run=fake.run,
                        directory=tmp_path, remaining=lambda: 1700, baseline_tree=tid('T0'), env_identity='img',
                        log=lambda *a, **k: None)


TASK = 'Repair it.\nrequirement_file: requirement.md\n'


def test_examples_drive_feedback_and_block_an_unseen_failure_once(tmp_path):
    fake = Fake()
    loop = loop_for(tmp_path, fake, ['cat > /testbed/.contractedge-deps/ce-examples/main.go', 'ce-example-run',
                                     'echo CE_DRIVER_DONE'])
    loop.prepare(TASK)
    snap = loop.store.load()
    assert snap['driver']['status'] == 'DONE' and snap['driver']['active']
    assert {f for f, ok in snap['examples']['trigger']['usable'].items() if ok} == {'terraform_id'}
    assert 'terraform_id (id from GetIDFn): expected a-vpce-1x9; original version gives vpce-1/subnet-2' in loop.intro
    assert 'Write an observation driver EARLY' not in loop.intro and fake.runs == ['T0', 'base']   # helper's own run, then calibration
    fake.tree = 'T1'                                                  # an edit that does not fix it
    note = loop.after_action('sed -i s/a/b/ config/x.go', {'output': '', 'returncode': 0}, tid('T0'), tid('T1'))
    assert 'terraform_id: FAIL (NOT MET); observed vpce-1/subnet-2, expected a-vpce-1x9' in note
    assert 'control [preserve] terraform_id: PASS' in note
    assert loop.before_submit(tid('T1')) is None                           # already shown: no block
    fake.tree = 'T2'
    note = loop.after_action('sed -i s/b/c/ config/x.go', {'output': '', 'returncode': 0}, tid('T1'), tid('T2'))
    assert 'trigger [target] terraform_id: PASS' in note
    assert loop.rank(tid('T2')) > loop.rank(tid('T1'))
    summary = loop.finish('Submitted')
    assert [r['status'] for r in summary['examples']['submitted']] == ['PASS', 'PASS']


def test_an_unseen_example_failure_blocks_submission_once(tmp_path):
    fake = Fake()
    loop = loop_for(tmp_path, fake, ['cat > /testbed/.contractedge-deps/ce-examples/main.go', 'echo CE_DRIVER_DONE'])
    loop.prepare(TASK)
    fake.tree = 'T1'
    reminder = loop.before_submit(tid('T1'))
    assert 'Before submitting: the example check' in reminder and 'shown once' in reminder
    assert loop.before_submit(tid('T1')) is None


def test_helper_edits_to_the_source_are_undone(tmp_path):
    fake = Fake()
    loop = loop_for(tmp_path, fake, ['sed -i s/x/y/ config/x.go', 'cat > /testbed/.contractedge-deps/ce-examples/main.go',
                                     'echo CE_DRIVER_DONE'])
    loop.prepare(TASK)
    assert fake.tree == 'T0' and loop.store.load()['driver']['active']
    assert any('checkout -q -- .' in c for c in fake.commands)


def test_no_driver_from_the_helper_and_a_changed_driver_is_recalibrated(tmp_path):
    fake = Fake(driver_writes=False)
    loop = loop_for(tmp_path, fake, ['cat > x', 'echo CE_DRIVER_DONE'])
    loop.prepare(TASK)
    assert 'hash' not in loop.store.load()['driver'] and 'Write an observation driver EARLY' in loop.intro
    fake = Fake()
    loop = loop_for(tmp_path / 'b', fake, ['cat > /testbed/.contractedge-deps/ce-examples/main.go', 'echo CE_DRIVER_DONE'])
    loop.prepare(TASK)
    fake.files['/testbed/.contractedge-deps/ce-examples/main.go'] = 'package main // edited by the executor'
    fake.tree = 'T1'
    note = loop.after_action('sed -i x config/x.go', {'output': '', 'returncode': 0}, tid('T0'), tid('T1'))
    assert 'Example driver calibrated on a copy of the original version: 2 field(s) usable' in note
    assert 'terraform_id: FAIL' in note and fake.runs == ['base', 'base', 'T1']
    assert loop.store.load()['driver']['calibrations'] == 2


def test_writer_reads_dsml_commands_and_caps_format_errors(tmp_path):
    dsml = ('<｜｜DSML｜｜ invoke name="bash"> <｜｜DSML｜｜ parameter name="command" string="true">'
            'ls /testbed</｜｜DSML｜｜ parameter> </｜｜DSML｜｜ invoke>')
    replies = [dsml, 'no command here', 'THOUGHT: done\n\n```bash\necho CE_DRIVER_DONE\n```']

    class M:
        def query(self, messages, **kwargs):
            return {'content': replies.pop(0)}
    ran = []
    writer = ex.DriverWriter(M(), lambda c, t: ran.append(c) or {'output': '', 'returncode': 0}, 4, lambda: False,
                             tmp_path / 't.json')
    assert writer.write('brief', lambda *a, **k: None) == {'status': 'DONE', 'steps': 1, 'format_errors': 1}
    assert ran == ['ls /testbed'] and len(json.loads((tmp_path / 't.json').read_text())) == 7

    class Silent:
        def query(self, messages, **kwargs):
            return {'content': ''}
    result = ex.DriverWriter(Silent(), lambda c, t: {}, 16, lambda: False).write('brief', lambda *a, **k: None)
    assert result == {'status': 'FORMAT', 'steps': 0, 'format_errors': 6}


def test_behavior_variant_without_examples_asks_no_examples(tmp_path):
    seen = []

    class M:
        def query(self, messages, **kwargs):
            seen.append(messages[0]['content'])
            return {'content': '{"requirements": []}'}
    BehaviorLoop(VARIANTS['behavior'], model=M(), run=Fake().run, directory=tmp_path, remaining=lambda: 1700,
                 baseline_tree='T0', env_identity='img', log=lambda *a, **k: None).prepare(TASK)
    assert '"examples"' not in seen[0]


def test_examples_need_feedback_and_add_only_their_switch():
    with pytest.raises(ValueError, match='needs behavior_feedback'):
        Variant.from_json({**VARIANTS['behavior-requirements-only'].to_json(), 'name': 'x', 'behavior_examples': True})
    a, b = VARIANTS['behavior'].to_json(), VARIANTS['behavior-examples'].to_json()
    assert {k for k in a if a[k] != b[k]} == {'name', 'behavior_examples'}


def test_the_executor_writes_the_driver_and_it_is_calibrated_on_the_original_version(tmp_path):
    fake = Fake()
    loop = loop_for(tmp_path, fake, [], VARIANTS['behavior-examples'])
    loop.prepare(TASK)
    assert 'driver' not in loop.store.load() and fake.runs == []
    assert 'terraform_id (id from GetIDFn): expected a-vpce-1x9' in loop.intro and 'CE_OBSERVE' in loop.intro
    reminder = loop.before_submit(tid('T0'))                           # examples exist, no driver yet: once
    assert 'no calibrated driver checks them yet' in reminder and loop.before_submit(tid('T0')) is None
    fake.files[DRIVER] = 'package main'                                # the executor writes it (no source change)
    note = loop.after_action('cat > ' + DRIVER, {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    assert 'calibrated on a copy of the original version: 2 field(s) usable' in note
    assert 'trigger [target] terraform_id: FAIL' in note and fake.runs == ['base', 'T0']
    fake.tree = 'T2'
    note = loop.after_action('sed -i s/b/c/ config/x.go', {'output': '', 'returncode': 0}, tid('T0'), tid('T2'))
    assert 'trigger [target] terraform_id: PASS' in note and 'calibrated' not in note


def test_baseline_runs_use_a_copy_of_the_original_tree():
    command = ex.run_command('ab12cd34')
    assert 'archive ab12cd34' in command and 'GOFLAGS=-trimpath' in command and 'cd /work/.contractedge-deps/ce-base' in command
    with pytest.raises(ValueError):
        ex.run_command('HEAD; rm -rf /')


def test_near_miss_names_are_read_as_the_defined_ones():
    snap, _ = defined()
    observed, renamed = ex.resolve_names({('trigger-subnet-association', 'Terraform-ID'): 'a',
                                          ('control', 'terraform_id'): 'b', ('triggercontrol', 'x'): 'c',
                                          ('other', 'y'): 'd'}, snap)
    assert observed == {('trigger', 'terraform_id'): 'a', ('control', 'terraform_id'): 'b',
                        ('triggercontrol', 'x'): 'c', ('other', 'y'): 'd'}      # two fits or none: left as printed
    assert renamed == {'trigger-subnet-association.Terraform-ID': 'trigger.terraform_id'}
    observed, _ = ex.resolve_names({('trigger-a', 'terraform_id'): 'x', ('trigger', 'terraform_id'): 'y'}, snap)
    assert observed == {('trigger', 'terraform_id'): 'y'}                      # the exact name wins


def test_skeleton_prints_every_defined_field():
    snap, _ = defined()
    text = ex.skeleton(snap)
    assert text.startswith('package main') and 'func ceObserve(example, field string, value any)' in text
    assert 'ceObserve("trigger", "terraform_id", nil) // TODO id from GetIDFn' in text
    assert 'ceObserve("control", "terraform_id", nil)' in text and 'a-vpce-1x9' not in text   # no expected values


class RenamingFake(Fake):
    def run(self, command, timeout):
        if command.startswith('ce-run'):
            self.runs.append('base' if 'ce-base' in command else self.tree)
            return {'output': 'go: build noise\nCE_OBSERVE {"example": "trigger-case", "field": "terraform_id", '
                              '"value": "vpce-1/subnet-2"}\npanic: nil map\nexit status 2\n', 'returncode': 1}
        return super().run(command, timeout)


def test_calibration_note_shows_renamings_and_the_driver_output(tmp_path):
    fake = RenamingFake()
    loop = loop_for(tmp_path, fake, [], VARIANTS['behavior-examples'])
    loop.prepare(TASK)
    assert any('skeleton.go.txt' in c for c in fake.commands) and 'skeleton.go.txt' in loop.intro
    fake.files[DRIVER] = 'package main'
    note = loop.after_action('cat > ' + DRIVER, {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    assert '- read trigger-case.terraform_id as trigger.terraform_id' in note
    assert 'dropped control.terraform_id: not observed' in note
    assert 'Driver output on the original version (last lines):' in note and 'panic: nil map' in note


def test_examples_stop_when_no_driver_is_calibrated_by_the_deadline(tmp_path):
    fake = Fake()
    clock = {'left': 1700}
    loop = BehaviorLoop(VARIANTS['behavior-examples'], model=Model([]), run=fake.run, directory=tmp_path,
                        remaining=lambda: clock['left'], baseline_tree=tid('T0'), env_identity='img',
                        log=lambda *a, **k: None)
    loop.prepare(TASK)
    loop.after_action('ls', {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    clock['left'] = 600                                                # 67% of 1800 s used, still no driver
    note = loop.after_action('ls', {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    assert 'Stop working on the driver' in note and loop.store.load()['examples_abandoned'] == 0.67
    fake.files[DRIVER] = 'package main'                                # a late driver is not calibrated any more
    note = loop.after_action('cat > ' + DRIVER, {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    assert 'calibrated' not in note and 'Stop working' not in note and fake.runs == []
    assert loop.before_submit(tid('T0')) is None                       # no "write the driver" reminder either


def test_a_calibrated_driver_is_never_abandoned(tmp_path):
    fake = Fake()
    clock = {'left': 1700}
    loop = BehaviorLoop(VARIANTS['behavior-examples'], model=Model([]), run=fake.run, directory=tmp_path,
                        remaining=lambda: clock['left'], baseline_tree=tid('T0'), env_identity='img',
                        log=lambda *a, **k: None)
    loop.prepare(TASK)
    fake.files[DRIVER] = 'package main'
    loop.after_action('cat > ' + DRIVER, {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    clock['left'] = 500                                                # 72% used, the driver is calibrated
    fake.tree = 'T2'
    note = loop.after_action('sed -i x config/x.go', {'output': '', 'returncode': 0}, tid('T0'), tid('T2'))
    assert 'Stop working' not in note and 'trigger [target] terraform_id: PASS' in note


def test_example_and_field_names_are_normalised_not_rejected():
    snap = behavior.empty()
    delta = ex.apply_examples(snap, [{'name': 'subnet association trigger', 'kind': 'target', 'source_refs': REF,
                                      'fields': [{'name': 'external name', 'expected': 'vpce-1/subnet-2'}]},
                                     {'name': '  ', 'kind': 'target', 'source_refs': REF,
                                      'fields': [{'name': 'x', 'expected': None}]}], sources())
    assert delta['added'] == ['subnet-association-trigger'] and len(delta['rejected']) == 1
    assert snap['examples']['subnet-association-trigger']['fields']['external-name']['mode'] == 'LITERAL'


class TimedFake(Fake):
    def __init__(self):
        super().__init__()
        self.timeouts = []

    def run(self, command, timeout):
        if command.startswith('ce-run'):
            self.timeouts.append(timeout)
        return super().run(command, timeout)


def test_a_late_driver_is_still_calibrated_within_the_remaining_time(tmp_path):
    fake = TimedFake()
    clock = {'left': 1700}
    loop = BehaviorLoop(VARIANTS['behavior-examples'], model=Model([]), run=fake.run, directory=tmp_path,
                        remaining=lambda: clock['left'], baseline_tree=tid('T0'), env_identity='img',
                        log=lambda *a, **k: None)
    loop.prepare(TASK)
    clock['left'] = 800                                                # a 900 s calibration no longer fits
    fake.files[DRIVER] = 'package main'
    note = loop.after_action('cat > ' + DRIVER, {'output': '', 'returncode': 0}, tid('T0'), tid('T0'))
    assert 'calibrated on a copy of the original version' in note
    assert fake.timeouts[0] == 800 - 90 - 60 and fake.runs == ['base', 'T0']


def test_a_literal_that_contradicts_the_quoted_record_is_not_used():
    # The UPJET-AWS-2117 dev run: expected the state id for external_name, the quoted record says otherwise.
    record = ('{"trigger": {"external_name": "vpce-1/subnet-2", "terraform_state": {"id": "a-vpce-1x9"}}}\n'
              'metadata:\n  annotations:\n    crossplane.io/external-name: vpce-1/subnet-2\n')
    path = '/inputs/inputs/r.json'
    s = behavior.Sources('Repair it.', lambda p: record if p == path else None)
    s.text(path)
    refs = [{'source': 'file', 'path': path, 'quote': '"external_name": "vpce-1/subnet-2", "terraform_state": {"id": "a-vpce-1x9"}'}]
    yaml_refs = [{'source': 'file', 'path': path, 'quote': 'crossplane.io/external-name: vpce-1/subnet-2'}]
    snap = behavior.empty()
    ex.apply_examples(snap, [
        {'name': 'trigger', 'kind': 'target', 'source_refs': refs,
         'fields': [{'name': 'external_name', 'expected': 'a-vpce-1x9'},        # contradicts the record
                    {'name': 'rebuilt_id', 'expected': 'a-vpce-1x9'},           # no such key quoted: literal
                    {'name': 'id', 'expected': 'a-vpce-1x9'}]},                 # agrees with the record
        {'name': 'annotated', 'kind': 'target', 'source_refs': yaml_refs,
         'fields': [{'name': 'external_name', 'expected': 'other'}, {'name': 'external-name', 'expected': 'vpce-1/subnet-2'}]}],
        s)
    fields = snap['examples']['trigger']['fields']
    assert fields['external_name']['mode'] == 'DIFF' and 'contradicts the quoted record' in fields['external_name']['note']
    assert fields['rebuilt_id']['mode'] == 'LITERAL' and fields['id']['mode'] == 'LITERAL'
    yaml = snap['examples']['annotated']['fields']
    assert yaml['external_name']['mode'] == 'DIFF' and yaml['external-name']['mode'] == 'LITERAL'
