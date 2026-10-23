from unittest.mock import patch
from contractedge.development import public_demo
from contractedge.orchestration.state import RepairEngine

def test_final_state_projection_preserves_primary_unknown_execution(tmp_path):
    factory = public_demo._provider
    def wrapped(config, case, runner):
        inner, counter = factory(config, case, runner)
        class UnknownExecution:
            def complete_json(self, request):
                if request.prompt_version == 'topomen-source-execution.v1':
                    raise TimeoutError('root fixture primary unknown execution')
                return inner.complete_json(request)
        return UnknownExecution(), counter
    original_projection = RepairEngine.project
    def fail_final_projection(self, **view):
        if view.get('status') == 'INFRASTRUCTURE_ERROR':
            raise OSError('root fixture state view failure')
        return original_projection(self, **view)
    with patch.object(public_demo, '_provider', wrapped), patch.object(RepairEngine, 'project', fail_final_projection):
        result = public_demo.run_demo('reload-state', tmp_path / 'run')
    assert result['status'] == 'INFRASTRUCTURE_ERROR'
    import json
    report = json.loads((tmp_path / 'run/repair/repair.trace.json').read_text())
    assert any('primary unknown execution' in error for error in report['errors'])
    calls = json.loads((tmp_path / 'run/repair/model_calls.json').read_text())
    assert len(calls) == 3
    assert 'TimeoutError' in calls[-1]['error']
    assert any('state view failure' in error for error in report['recording_errors'])
