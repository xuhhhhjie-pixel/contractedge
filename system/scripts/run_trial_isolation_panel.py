#!/usr/bin/env python3
"""Real C2 isolation acceptance v2. --plan never contacts Docker or a model API.

The execute path uses only the published synthetic model providers, but every
candidate and independent observer runs through the actual RuntimeSession.
No RuntimeSession/Docker output is mocked. Faults occur only after all opened
sessions have independently confirmed cleanup. No unowned cleanup is attempted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


POINTS = ('normal', 'after-applied', 'after-observed', 'before-selection')
DIALECTS = ('topomen-native', 'modify-only')
EXIT_FAULT = 73


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_source(system, binding, expected_repair_sha256=None):
    system = Path(system).resolve(strict=True)
    require((system / 'src/contractedge').is_dir(), '--system must point to the actual system directory')
    root = system.parent
    repair_name = 'system/src/contractedge/orchestration/repair.py'
    effective_files = dict(binding['files'])
    if expected_repair_sha256 is not None:
        require(re.fullmatch(r'[0-9a-f]{64}', expected_repair_sha256) is not None,
                '--expected-repair-sha256 must be one complete lowercase SHA256')
        require(repair_name in effective_files, 'Binding does not include the explicitly approved repair source')
        effective_files[repair_name] = expected_repair_sha256
    for name, expected in effective_files.items():
        path = root / name
        require(path.is_file() and sha(path) == expected, 'Frozen implementation/input differs: ' + name)
    result = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', binding['base_commit'], 'HEAD'],
                            capture_output=True, text=True)
    require(result.returncode == 0, 'Formal source does not descend from the recorded C1 base')
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    return {'current_head': head, 'base_commit': binding['base_commit'],
            'original_binding_source_scripts_aggregate': binding['source_scripts_aggregate'],
            'original_binding_repair_sha256': binding['files'].get(repair_name),
            'explicit_expected_repair_sha256': expected_repair_sha256,
            'effective_file_mapping_sha256': hashlib.sha256(json.dumps(effective_files, sort_keys=True,
                ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest(),
            'effective_file_mapping_digest_definition': 'SHA256 of UTF-8 JSON file-to-SHA256 mapping, sorted keys, compact separators, ensure_ascii=False',
            'bound_files_verified': len(effective_files)}


def import_system(system):
    system = Path(system).resolve()
    sys.path[:0] = [str(system / 'src'), str(system)]
    os.environ['CONTRACTEDGE_TOPOMEN_SOURCE'] = str(system / 'vendor/topomen')


def environment(system, case):
    # Explicit whitelist: do not propagate API credentials or user provider config.
    home, temporary = case / 'control-home', case / 'control-tmp'
    home.mkdir(exist_ok=True); temporary.mkdir(exist_ok=True)
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8', 'HOME': str(home),
            'TMPDIR': str(temporary), 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONPATH': str(system / 'src') + os.pathsep + str(system),
            'CONTRACTEDGE_TOPOMEN_SOURCE': str(system / 'vendor/topomen')}


def no_mock(value):
    if isinstance(value, dict):
        require(not value.get('mock_boundary') and not value.get('fixture_only'), 'Mock/fixture runtime receipt is forbidden')
        for item in value.values(): no_mock(item)
    elif isinstance(value, list):
        for item in value: no_mock(item)


def docker_readonly(spec, case, directory, arguments):
    """Only scoped ps/inspect audits; this harness never issues rm/prune/stop."""
    executable = shutil.which('docker', path='/usr/bin:/bin')
    require(executable is not None, 'Actual Docker CLI is missing from the RuntimeSession command path')
    directory.mkdir(parents=True, exist_ok=True)
    config = case / 'audit-docker-config'; config.mkdir(exist_ok=True)
    command = [executable, '--host', spec['docker_host'], '--config', str(config), *arguments]
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(case / 'control-home'), 'LANG': 'C.UTF-8'}
    if spec.get('docker_api_version'): env['DOCKER_API_VERSION'] = spec['docker_api_version']
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    number = len(list(directory.glob('command-*.json')))
    write(directory / ('command-' + str(number).zfill(4) + '.json'),
          {'argv': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
           'scope': 'read-only audit of this session token/container identity'})
    return result


def audit_runtime(case, runtime_config, label, expected_count):
    """Verify actual persisted attestations, bytes and daemon-side disappearance."""
    from contractedge.execution.public_observer import isolated_observation_complete
    from contractedge.contracts.validation import canonical_digest
    repair = case / 'repair'
    manifests = sorted(repair.glob('**/runtime/session.json'))
    require(len(manifests) == expected_count, 'Unexpected actual RuntimeSession count: ' + str(len(manifests)))
    summaries = []
    for index, path in enumerate(manifests):
        value = read(path); no_mock(value)
        token = value.get('session_token', '')
        require(re.fullmatch(r'[0-9a-f]{32}', token) is not None, 'Missing real session ownership token')
        require(value.get('schema_version') == 'ce.runtime_session.v1' and value.get('status') == 'CLOSED',
                'RuntimeSession did not close; leave ownership evidence for root, do not guess cleanup')
        spec = value['spec']
        require(spec['image'] == runtime_config['image'] and spec['docker_host'] == runtime_config['docker_host'],
                'Actual session rebound runtime image/daemon')
        require(Path(spec['session_root']).resolve() == path.parent.resolve()
                and path.parent.resolve().is_relative_to(case.resolve()), 'Runtime session is not owned by this case')
        require(spec['cpus'] == 2 and spec['memory_bytes'] == 2 * 1024 ** 3, 'The frozen 2CPU/2GiB limit changed')
        runs = value.get('runs', [])
        require(len(runs) == 2 and {run.get('role') for run in runs} == {'candidate', 'observer'},
                'Candidate and independent observer must both actually execute')
        ids = []
        for run in runs:
            no_mock(run)
            cid = run.get('container_id', '')
            require(re.fullmatch(r'[0-9a-f]{64}', cid) is not None, 'Actual container ID is absent')
            ids.append(cid)
            require(run.get('planned_container_name') == 'ce-' + token + '-' + run['run_id'], 'Run ownership differs')
            require(run['status'] == 'EXECUTION_PASS' and run.get('phase') == 'FINISHED', 'Actual runtime run did not finish')
            require(run.get('cleanup', {}).get('status') == 'CONFIRMED'
                    and run.get('close_cleanup', {}).get('status') == 'CONFIRMED', 'Run/close cleanup was not confirmed')
            attest = run.get('attestation', {})
            require(attest.get('status') == 'VERIFIED' and isinstance(attest.get('kernel'), dict),
                    'Requested resource settings are insufficient without actual kernel attestation')
            inspected, kernel = attest['inspect'], attest['kernel']
            host = inspected['host_config']
            require(inspected['container_id'] == cid and inspected['network_mode'] == 'none'
                    and inspected['user'] == str(spec['uid']) + ':' + str(spec['gid']) and spec['uid'] != 0,
                    'Actual container identity/network/UID differs')
            require(host['ReadonlyRootfs'] is True and host['Privileged'] is False
                    and host['NanoCpus'] == 2_000_000_000 and host['Memory'] == 2 * 1024 ** 3
                    and host['MemorySwap'] == host['Memory'], 'Actual inspect resource/security limits differ')
            quota, period = kernel['cpu_max'].split()
            require(quota not in {'max', '-1'} and 0 < int(quota) / int(period) <= 2.001
                    and 0 < int(kernel['memory_max']) <= 2 * 1024 ** 3
                    and kernel['uid'] == spec['uid'] and int(kernel['cap_eff'], 16) == 0
                    and kernel['no_new_privs'] == '1' and kernel['seccomp'] == '2', 'Actual kernel limits were not verified')
            mounts = inspected['mounts']
            wanted = {'/source', '/inputs'} if run['role'] == 'candidate' else {'/observations', '/checks'}
            require({mount['Destination'] for mount in mounts} == wanted
                    and all(mount['Type'] == 'bind' and mount['RW'] is False for mount in mounts),
                    'Candidate/observer mount separation differs')
            if run['role'] == 'candidate': require(run.get('source_unchanged') is True, 'Candidate changed original source')
            for channel in ('stdout', 'stderr'):
                output = Path(run[channel + '_path']).resolve(strict=True)
                require(output.is_relative_to(path.parent.resolve()) and sha(output) == run[channel + '_sha256']
                        and output.stat().st_size == run[channel + '_bytes'], 'Actual runtime output bytes changed')
        require(len(set(ids)) == 2, 'Observer reused the candidate container')
        observation = read(path.parent.parent / 'observation.json')
        no_mock(observation)
        require(isolated_observation_complete(observation), 'Independent public observation is incomplete or stale')
        require(canonical_digest(observation['runtime']['close']) == canonical_digest(value), 'Published close receipt differs from disk')
        commands = case / 'audit' / label / ('session-' + str(index))
        listed = docker_readonly(runtime_config, case, commands,
            ['ps', '-aq', '--no-trunc', '--filter', 'label=contractedge.runtime.session=' + token])
        require(listed.returncode == 0 and not listed.stdout.strip(), 'Owned session container still exists or absence is unverified')
        for cid in ids:
            inspected = docker_readonly(runtime_config, case, commands, ['container', 'inspect', cid])
            absent = inspected.stderr.casefold()
            require(inspected.returncode != 0 and ('no such object' in absent or 'no such container' in absent),
                    'Actual container disappearance was not independently confirmed')
        summaries.append({'session_path': str(path), 'session_sha256': sha(path), 'session_token': token,
                          'container_ids': ids, 'status': 'CLOSED', 'cleanup': 'CONFIRMED', 'daemon_absence': 'CONFIRMED',
                          'observation_sha256': sha(path.parent.parent / 'observation.json')})
    require(len({item['session_token'] for item in summaries}) == len(summaries), 'Session token was reused')
    return summaries


def expected_sessions(point):
    return 1 if point == 'after-applied' else 2


def validate_review_path(case, point, dialect, calls):
    """Verify actual dispatch stages and the independently evidenced local review.

    These finite fixtures bind all three postconditions to public ALL_OF probes.
    Production ModelPublicReviewer therefore resolves all mechanical checks before
    its model branch; normal/selection cases still require a real review receipt.
    """
    from contractedge.contracts.validation import canonical_digest
    from contractedge.execution.public_observer import enforce_public_profile, isolated_observation_complete
    from contractedge.execution.support import deterministic_check_result
    expected_prompts = ['diagnosis.v1.1.24', 'changeset.v1.1.14',
                        'topomen-source-execution.v1' if dialect == 'topomen-native'
                        else 'modify-only-literal-execution.v1']
    require([call.get('prompt_version') for call in calls] == expected_prompts,
            'Actual diagnosis/changeset/consumer dispatch sequence differs; no synthetic fourth review call is expected')
    repair = case / 'repair'; directory = repair / 'rounds/0'
    review_path = directory / 'public-review.json'
    receipt_paths = sorted((repair / 'journal/stages/review/round-0').glob('revision-*.json'))
    if point in {'after-applied', 'after-observed'}:
        require(not review_path.exists() and not receipt_paths,
                'Fault boundary unexpectedly completed a review before its intended effect')
        return {'actual_prompt_versions': expected_prompts, 'review': 'NOT_REACHED_AT_FAULT_BOUNDARY',
                'model_review_dispatches': 0}
    require(len(receipt_paths) == 1, 'Exactly one actual local public-review stage receipt is required')
    plan_path = directory / 'public-review-plan.json'
    observation_path = directory / 'public-observation.json'
    delivery_path = directory / 'review-evidence.delivery.json'
    plan, observation, review, delivery = map(read, (plan_path, observation_path, review_path, delivery_path))
    require(isolated_observation_complete(observation), 'Local review lacks the actual complete isolated public observation')
    snapshot = observation['snapshot_id']; input_digest = plan['input_digest']
    require(not plan.get('preserve_constraints') and not plan.get('open_obligations'),
            'Finite fixture review acquired additional obligations')
    bindings = {'P1': 'selected', 'P2': 'disabled', 'P3': 'stable'}
    conditions = plan['postconditions']
    require(len(conditions) == 3 and {condition['id'] for condition in conditions} == set(bindings),
            'Expected public target/preservation postconditions differ')
    expected_checks = []
    for condition in conditions:
        require(condition.get('verification_kind') == 'PUBLIC_BEHAVIOR'
                and condition.get('check_coverage') == 'ALL_OF'
                and condition.get('behavior_check_refs') == ['contractedge-public-process-v1:' + bindings[condition['id']]],
                'Postcondition is not completely bound to its predeclared finite public predicate')
        result = deterministic_check_result(condition, observation['observations'], snapshot_id=snapshot)
        require(result is not None and result['status'] == 'PASS'
                and result.get('decision_method') == 'BOUND_CHECK_AGGREGATION',
                'Actual fresh observer evidence does not mechanically resolve every public check')
        expected_checks.append({'id': condition['id'], **result})
    require(review.get('checks') == expected_checks and review.get('method') == 'BOUND_CHECK_AGGREGATION'
            and review.get('decision') == 'PASS' and review.get('independent_hidden_evaluation') is False,
            'Actual local review differs from the complete predeclared check aggregation')
    enforced = enforce_public_profile(review, observation, required=True)
    require(review.get('required_public_profile') == enforced['required_public_profile']
            and review['required_public_profile'].get('binding_valid') is True
            and review['required_public_profile'].get('decision') == 'PASS',
            'Actual local review lacks its mandatory independently observed public-profile binding')
    require(delivery.get('schema_version') == 'ce.evidence_delivery.v1' and delivery.get('status') == 'COMPLETE'
            and delivery.get('snapshot_id') == snapshot and not delivery.get('errors') and not delivery.get('omitted'),
            'Review evidence was incomplete, stale or omitted')
    require(canonical_digest({key: value for key, value in delivery.items() if key != 'receipt_digest'}) == delivery['receipt_digest']
            and canonical_digest(observation['observations']) == delivery['input_records_digest'],
            'Review evidence delivery receipt/input digest differs')
    delivered = [observation['observations'][row['input_index']] for row in delivery['selected']]
    for row, record in zip(delivery['selected'], delivered):
        require(row['ref'] == {key: record[key] for key in ('id', 'snapshot_id', 'content_digest')},
                'Review delivery reference differs from actual public evidence')
    require(len(delivered) == 3 and {record['id'] for record in delivered} == {'OBS-' + value for value in bindings.values()}
            and canonical_digest(delivered) == delivery['records_digest'], 'Review delivery did not include every complete public predicate')
    receipt = read(receipt_paths[0])
    require(receipt.get('schema_version') == 'ce.stage_receipt.v1' and receipt.get('stage') == 'review'
            and receipt.get('status') == 'PASS' and receipt.get('attempt_id') == 'round-0'
            and receipt.get('snapshot_id') == snapshot and receipt.get('input_digest') == input_digest
            and receipt.get('call_refs') == []
            and canonical_digest({key: value for key, value in receipt.items() if key != 'receipt_digest'}) == receipt['receipt_digest'],
            'Local review stage receipt is stale, altered, or claims an unrecorded model dispatch')
    for group in ('input_refs', 'output_refs', 'projection_refs'):
        for reference in receipt[group]:
            artifact = (repair / reference['relative_path']).resolve(strict=True)
            require(artifact.is_relative_to(repair.resolve()) and sha(artifact) == reference['sha256']
                    and reference['input_digest'] == input_digest, 'Review receipt artifact changed or belongs to another input')
    for group, live in (('input_refs', plan_path), ('input_refs', observation_path),
                        ('output_refs', review_path), ('projection_refs', delivery_path)):
        matches = [reference for reference in receipt[group]
                   if reference['relative_path'].endswith('/rounds/0/' + live.name)]
        require(len(matches) == 1 and matches[0]['sha256'] == sha(live),
                'Actual review receipt does not bind the exported ' + live.name)
    return {'actual_prompt_versions': expected_prompts, 'review': 'BOUND_CHECK_AGGREGATION',
            'model_review_dispatches': 0, 'independent_public_profile': 'COMPLETE_BOUND_PASS',
            'review_stage_receipt_sha256': sha(receipt_paths[0]), 'review_sha256': sha(review_path),
            'review_delivery_sha256': sha(delivery_path), 'actual_observation_sha256': sha(observation_path)}


def child(spec_path, phase):
    spec = read(spec_path); case = Path(spec['case_root']); system = Path(spec['system'])
    require(sha(spec['binding']) == spec['binding_sha256'], 'Original source binding changed after parent verification')
    verify_source(system, read(spec['binding']), spec.get('expected_repair_sha256')); import_system(system)
    from contractedge.development.public_demo import (IsolatedPublicExampleRunner, SyntheticExampleProvider,
                                                     count_tokens, default_config, freeze_example)
    from contractedge.execution.consumers.direct import ModifyOnlyConsumer
    from contractedge.orchestration.repair import repair_case
    from contractedge.orchestration.trial import TrialSession
    from contractedge.source.snapshot import compute_snapshot
    from scripts.run_consumer_host_fixture import ModifyFixtureProvider
    public = case / 'frozen-public/public'; runtime = spec['runtime']
    runner = IsolatedPublicExampleRunner(public, 'config-binding', runtime)
    require(runner.runtime_kind == 'isolated-public-process' and runner.requires_public_profile, 'Host fallback is forbidden')
    if phase == 'fresh':
        freeze_example('config-binding', case, runtime='isolated')
        config = {**default_config('config-binding'), 'public_runtime': 'isolated',
                  'public_runtime_config': runtime, 'max_rounds': 1, 'candidate_guard_enabled': True,
                  'run_id': 'c2-real-' + spec['dialect'] + '-' + spec['point']}
        consumer = None
        if spec['dialect'] == 'modify-only':
            config['execution_backend'] = 'direct'; consumer = ModifyOnlyConsumer()
            config.pop('topomen_source_root', None); config.pop('topomen_source_sha256', None)
            provider = ModifyFixtureProvider('config-binding', runner)
        else: provider = SyntheticExampleProvider('config-binding', runner)
        require(config['provider'] == 'synthetic' and not {'base_url', 'api_key_env', 'money_budget'}.intersection(config),
                'Only original zero-cost synthetic providers are admitted')
        write(case / 'config.json', config)
        point = spec['point']
        def stop_safely(trial):
            events = trial.events(); kinds = [event['kind'] for event in events]
            require(read(case / 'repair/rounds/0/execution.json')['status'] == 'APPLIED',
                    'Fault boundary requires an actual applied consumer edit')
            actual_snapshot = compute_snapshot(case / 'repair/workspace/source/repo').snapshot_id
            initial_snapshot = read(case / 'repair/initial-public-observation.json')['snapshot_id']
            require(actual_snapshot != initial_snapshot, 'Fault boundary requires an actually changed trial source')
            if point in {'after-applied', 'after-observed'}:
                require(kinds[-1] == ('APPLIED' if point == 'after-applied' else 'OBSERVED'),
                        'Fault did not follow the required immutable completed effect')
            else:
                require('APPLIED' in kinds and 'OBSERVED' in kinds and 'SELECTION_INTENT' not in kinds,
                        'Pre-selection fault crossed an unintended commit boundary')
            sessions = audit_runtime(case, runtime, 'before-fault', expected_sessions(point))
            write(case / 'fault.marker.json', {'fault_point': point, 'trial_id': trial.trial_id,
                  'all_opened_sessions_closed': True, 'actual_sessions': sessions,
                  'source_snapshot': actual_snapshot, 'initial_snapshot': initial_snapshot,
                  'durable_trial_event_kinds': kinds,
                  'last_durable_trial_event_sha256': sha(trial.directory / (str(events[-1]['sequence']).zfill(6) + '.json'))})
            os._exit(EXIT_FAULT)
        if point in {'after-applied', 'after-observed'}:
            original = TrialSession.record
            wanted = 'APPLIED' if point == 'after-applied' else 'OBSERVED'
            def record(self, kind, **payload):
                event = original(self, kind, **payload)
                if kind == wanted: stop_safely(self)
                return event
            TrialSession.record = record
        elif point == 'before-selection':
            def select(self, *args, **kwargs): stop_safely(self)
            TrialSession.select = select
        report = repair_case(public, case / 'tools', 'config-binding', case / 'repair', provider, count_tokens,
                             config, public_runner=runner, consumer=consumer)
        write(case / 'fresh.result.json', report)
        return 0
    config = read(case / 'config.json'); ledger = case / 'repair/model_calls.json'
    before = sha(ledger); snapshot = compute_snapshot(case / 'repair/workspace/source/repo').snapshot_id
    if phase == 'native-refusal':
        require(spec['dialect'] == 'topomen-native', 'Native refusal case belongs to native only')
        try:
            repair_case(public, case / 'tools', 'config-binding', case / 'repair', None, None, config,
                        public_runner=runner, resume=True, consume_received_response=True)
        except ValueError as error:
            require('unsupported' in str(error).lower() and 'Native TopoMen' in str(error), 'Native refusal reason differs')
            require(sha(ledger) == before and compute_snapshot(case / 'repair/workspace/source/repo').snapshot_id == snapshot,
                    'Native replay refusal changed original source/calls')
            write(case / 'native-replay-refusal.json', {'status': 'REFUSED_AS_REQUIRED', 'reason': str(error),
                                                     'original_ledger_sha256': before})
            return 0
        raise RuntimeError('Native received-response replay must be refused')
    report = repair_case(public, case / 'tools', 'config-binding', case / 'repair', None, None, config,
                         public_runner=runner, resume=True)
    require(sha(ledger) == before, 'Recovery changed original dispatch/response ledger')
    write(case / (phase + '.result.json'), report)
    return 0


def invoke_child(args, case, phase):
    command = [sys.executable, str(Path(__file__).resolve()), '--internal-spec', str(case / 'harness.case.json'),
               '--internal-phase', phase]
    with (case / (phase + '.stdout')).open('wb') as out, (case / (phase + '.stderr')).open('wb') as error:
        process = subprocess.Popen(command, cwd=case, env=environment(Path(args.system), case), stdout=out, stderr=error)
        write(case / 'active-child.json', {'pid': process.pid, 'phase': phase, 'argv': command,
              'ownership': 'Only this case; do not kill arbitrary Docker containers. Runtime manifests retain tokens.'})
        # Do not impose a parent hard-kill while RuntimeSession could be active.
        # Existing repair/runtime deadlines perform owned cleanup themselves.
        code = process.wait()
    write(case / (phase + '.exit.json'), {'exit_code': code, 'argv': command})
    return code


def validate_result(case, point, dialect, sessions):
    from contractedge.source.snapshot import compute_snapshot
    initial = read(case / 'repair/initial-public-observation.json')
    require({row['id']: row['status'] for row in initial['required_public_profile']['checks']} ==
            {'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'}, 'Actual Broken/public preservation truth table differs')
    if point != 'after-applied':
        trial = read(case / 'repair/rounds/0/public-observation.json')
        require(all(row['status'] == 'PASS' for row in trial['required_public_profile']['checks']), 'Actual candidate public checks did not pass')
    report = read(case / ('fresh.result.json' if point == 'normal' else 'resume.result.json'))
    calls = read(case / 'repair/model_calls.json')
    require(all(isinstance(call.get('response_content'), str) and call.get('response_metadata', {}).get('zero_cost') is True
                for call in calls), 'Every model-bound call must be an actual recorded synthetic settlement')
    review_evidence = validate_review_path(case, point, dialect, calls)
    require(report['budget']['model_calls'] == len(calls), 'Original call allowance/facts differ')
    reserve = sum(call['prompt_tokens_reserved'] + call['output_tokens_reserved'] for call in calls)
    require(report['budget']['reserved_tokens'] == reserve, 'Original token reservations changed')
    source = case / 'repair/workspace/source/repo'; actual = compute_snapshot(source).snapshot_id
    selected = read(case / 'repair/selected-candidate.json'); state = read(case / 'repair/state.json')
    feedback = read(case / 'repair/selected-feedback.json'); patch = case / 'repair/candidate.patch'
    require(report['final_snapshot_id'] == actual == selected['snapshot_id'] == state['current_snapshot'],
            'Selected/source/state snapshot differs')
    require(state['selected']['snapshot_id'] == actual and report['last_feedback'] == feedback, 'Selected state/feedback differs')
    require(report['candidate'] is not None and sha(patch) == report['candidate']['patch_sha256']
            and patch.stat().st_size == report['candidate']['patch_bytes'], 'Actual exported patch differs')
    if feedback is not None: require(feedback['to_snapshot'] == actual and feedback['patch_digest'] == sha(patch), 'Selected feedback patch binding differs')
    execution = read(case / 'repair/rounds/0/execution.json')
    require(execution['status'] == 'APPLIED', 'Actual consumer execution did not apply an edit')
    if dialect == 'topomen-native':
        require(execution['backend']['agent_class'] == 'topomen.collaboration.base_agent.DefaultAgent', 'Actual native DefaultAgent was bypassed')
    else:
        require(execution['consumer_id'] == 'contractedge.modify_only_consumer.v1'
                and any(call['prompt_version'] == 'modify-only-literal-execution.v1' for call in calls), 'Independent consumer dialect was bypassed')
    if point == 'normal':
        require(report['status'] == 'PUBLIC_REVIEW_COMPLETE' and patch.stat().st_size > 0, 'Normal actual isolated closure failed')
        interpretation = 'Actual bounded public target and preservation checks PASS; not hidden/semantic generalization evidence.'
    else:
        require(report['status'] == 'RECOVERED_INCUMBENT' and report['final_snapshot_id'] == report['initial_snapshot_id']
                and patch.stat().st_size == 0 and feedback is None, 'Interrupted trial did not restore coherent incumbent')
        require(read(case / 'repeat-resume.result.json') == report, 'Repeated recovery changed the terminal decision/budget')
        require(report['recovery']['dispatches'] == 0 and report['recovery']['budget_reinitialized'] is False,
                'Recovery created a new request or budget')
        interpretation = 'Engineering recovery PASS: original Broken incumbent retained; do not label its target repaired.'
    return {'status': 'PASS', 'dialect': dialect, 'fault_point': point, 'source_snapshot': actual,
            'actual_runtime_sessions': len(sessions), 'actual_candidate_and_observer_containers': len(sessions) * 2,
            'model_calls': len(calls), 'original_reserved_tokens': reserve, 'selected_patch_sha256': sha(patch),
            'runtime_cleanup': 'CONFIRMED', 'daemon_absence': 'CONFIRMED',
            'actual_review_path': review_evidence, 'interpretation': interpretation}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--system', type=Path, help='Exact formal worktree/system directory')
    result.add_argument('--binding', type=Path, default=Path(__file__).with_name('source-binding.json'))
    result.add_argument('--expected-repair-sha256', help='Explicit single-file repair.py SHA256 override; every other binding remains mandatory')
    result.add_argument('--output', type=Path, help='New independent evidence directory')
    result.add_argument('--image', help='Trusted cached digest-pinned image; no pull is performed')
    result.add_argument('--docker-host', help='Explicit trusted local unix:/// Docker socket')
    result.add_argument('--docker-api-version')
    result.add_argument('--plan', action='store_true', help='Read-only preparation checks; no Docker/provider/runtime call')
    result.add_argument('--internal-spec', type=Path, help=argparse.SUPPRESS)
    result.add_argument('--internal-phase', choices=['fresh', 'native-refusal', 'resume', 'repeat-resume'], help=argparse.SUPPRESS)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.internal_spec:
        return child(args.internal_spec, args.internal_phase)
    require(all([args.system, args.output, args.image, args.docker_host]), '--system/--output/--image/--docker-host are required')
    require(re.fullmatch(r'(?:[^\s@]+@)?sha256:[0-9a-f]{64}', args.image) is not None, 'Image must be digest pinned')
    require(args.docker_host.startswith('unix:///'), 'Only an explicit trusted local Docker socket is accepted')
    if args.docker_api_version: require(re.fullmatch(r'1\.\d{2,3}', args.docker_api_version) is not None, 'Invalid Docker API version')
    args.system = args.system.resolve(strict=True); output = args.output.absolute()
    require(not output.resolve().is_relative_to(args.system.parent)
            and not args.system.parent.is_relative_to(output.resolve()), 'Evidence must be independent of formal source')
    binding = read(args.binding); identity = verify_source(args.system, binding, args.expected_repair_sha256)
    runtime = {'image': args.image, 'docker_host': args.docker_host}
    if args.docker_api_version: runtime['docker_api_version'] = args.docker_api_version
    cells = [{'dialect': dialect, 'point': point, 'expected_runtime_sessions': expected_sessions(point),
              'status': 'NOT_RUN'} for dialect in DIALECTS for point in POINTS]
    plan = {'status': 'PREPARED_NOT_RUN', 'harness_version': 2, 'identity': identity, 'binding_sha256': sha(args.binding),
            'harness_sha256': sha(__file__), 'cells': cells, 'real_model_http_calls': 0,
            'expected_actual_sessions': 14, 'expected_actual_candidate_observer_containers': 28,
            'runtime': runtime, 'scope': 'Actual isolation only when executed successfully; no host/mock equivalence claim.'}
    if args.plan:
        print(json.dumps(plan, indent=2)); return 0
    require(stat.S_ISSOCK(Path(args.docker_host[len('unix://'):]).stat().st_mode), 'Trusted Docker endpoint is not a local socket')
    require(not output.exists(), 'Output must be new; existing evidence is never reused or overwritten')
    output.mkdir(parents=True); import_system(args.system)
    write(output / 'plan.json', plan); summary = {**plan, 'status': 'RUNNING', 'formal_ready': False}
    write(output / 'summary.json', summary)
    for index, cell in enumerate(cells):
        case = output / (cell['dialect'] + '-' + cell['point']); case.mkdir()
        spec = {'case_root': str(case), 'system': str(args.system), 'binding': str(args.binding.resolve()),
                'binding_sha256': sha(args.binding), 'expected_repair_sha256': args.expected_repair_sha256,
                'runtime': runtime, 'dialect': cell['dialect'], 'point': cell['point']}
        write(case / 'harness.case.json', spec)
        try:
            code = invoke_child(args, case, 'fresh')
            require(code == (0 if cell['point'] == 'normal' else EXIT_FAULT), 'Fresh execution/fault did not reach its required boundary; inspect child stderr/report')
            ledger = sha(case / 'repair/model_calls.json')
            first_sessions = audit_runtime(case, runtime, 'after-child', expected_sessions(cell['point']))
            if cell['point'] != 'normal':
                require(read(case / 'fault.marker.json')['all_opened_sessions_closed'] is True, 'Fault occurred before owned cleanup')
                if cell['dialect'] == 'topomen-native': require(invoke_child(args, case, 'native-refusal') == 0, 'Native replay refusal failed')
                require(invoke_child(args, case, 'resume') == 0 and invoke_child(args, case, 'repeat-resume') == 0, 'Actual incumbent recovery failed')
                require(sha(case / 'repair/model_calls.json') == ledger, 'Recovery changed original model reservations/settlements')
            final_sessions = audit_runtime(case, runtime, 'after-recovery', expected_sessions(cell['point']))
            require(first_sessions == final_sessions, 'Recovery created/rebound runtime sessions or old receipts')
            cell.update(validate_result(case, cell['point'], cell['dialect'], final_sessions))
            write(case / 'acceptance.json', cell)
        except Exception as error:
            cell.update(status='FAIL', error=type(error).__name__ + ': ' + str(error),
                        ownership_note='No global cleanup attempted. Inspect this case runtime/session.json tokens before any manual action.')
            write(case / 'acceptance.json', cell); summary['status'] = 'FAIL'; write(output / 'summary.json', summary)
            print(json.dumps(summary, indent=2)); return 1
        write(output / 'summary.json', summary)
    summary['status'] = 'PASS'; summary['scope'] = 'All eight real isolated engineering cells passed; synthetic model responses only; not a benchmark/generalization result.'
    summary['actual_runtime_sessions'] = sum(cell['actual_runtime_sessions'] for cell in cells)
    summary['actual_candidate_observer_containers'] = sum(cell['actual_candidate_and_observer_containers'] for cell in cells)
    require(summary['actual_runtime_sessions'] == 14 and summary['actual_candidate_observer_containers'] == 28, 'Incomplete actual matrix')
    require(sha(args.binding) == plan['binding_sha256'], 'Original source binding changed during execution')
    verify_source(args.system, binding, args.expected_repair_sha256)
    inventory = {str(path.relative_to(output)): sha(path) for path in sorted(output.rglob('*'))
                 if path.is_file() and not path.is_symlink() and '.git' not in path.parts and path.name != 'summary.json'}
    write(output / 'artifacts.inventory.json', inventory)
    summary['inventory_sha256'] = sha(output / 'artifacts.inventory.json'); write(output / 'summary.json', summary)
    print(json.dumps(summary, indent=2)); return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
