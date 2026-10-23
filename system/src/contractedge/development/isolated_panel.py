"""Synthetic method panel runner using real RuntimeSession isolation only."""
import json
from pathlib import Path
import re

from contractedge.development.method_panel_fixtures import PanelRunner, case_definition
from contractedge.execution.panel_observer import (IsolatedPanelObserver, LIMIT_FIELDS,
    isolated_panel_observation_complete, enforce_isolated_panel_profile)
from contractedge.orchestration.repair import write_json


def validate_runtime_config(value):
    if not isinstance(value, dict) or set(value) - {'image', 'docker_host', 'docker_api_version', *LIMIT_FIELDS}:
        raise ValueError('Isolated panel requires only explicit image/socket/API and resource configuration')
    if not isinstance(value.get('image'), str) or not re.fullmatch(r'(?:[a-zA-Z0-9][a-zA-Z0-9./:_-]*@)?sha256:[a-f0-9]{64}', value['image']):
        raise ValueError('Isolated panel requires an already cached digest-pinned image')
    if not isinstance(value.get('docker_host'), str) or not value['docker_host'].startswith('unix:///'):
        raise ValueError('Isolated panel requires an explicit local Unix Docker socket')
    return dict(value)


class IsolatedPanelRunner(PanelRunner):
    requires_public_profile = True
    requires_requirement_profile = True
    runtime_kind = 'isolated-public-process'
    synthetic_only = True

    def __init__(self, public, case, runtime_config):
        super().__init__(public, case)
        self.observer = IsolatedPanelObserver(**validate_runtime_config(runtime_config), timeout_seconds=self.timeout_seconds)
        self.first_observation = True

    def enforce_requirement_profile(self, review, observation):
        return enforce_isolated_panel_profile(review, observation)

    def observe(self, workspace, output):
        workspace, output = Path(workspace).resolve(), Path(output).resolve()
        record = json.loads((workspace / 'workspace.json').read_text())
        self.observer.timeout_seconds = self.timeout_seconds
        report = self.observer.observe(record['source_root'], workspace / 'inputs/runtime-inputs.json', output,
            fixture_id=self.fixture_id)
        if report.get('status') == 'EXECUTION_PASS' and not isolated_panel_observation_complete(report):
            report.update(status='INVALID_OBSERVATION', isolated=False)
            report['errors'].append('Six-case isolated runtime/profile was not complete')
        if self.first_observation:
            self.first_observation = False
            states = {r['id']:r['status'] for r in (report.get('requirement_profile') or {}).get('checks', [])}
            if report['status'] != 'EXECUTION_PASS' or states != case_definition(self.fixture_id)['broken_profile']:
                report.update(status='INVALID_OBSERVATION', isolated=False)
                report['required_public_profile']['status'] = 'INCOMPLETE'
                report['errors'].append('Frozen Broken must establish the complete fixed target FAIL / preservation profile before model dispatch')
        write_json(output / 'observation.json', report)
        return report
