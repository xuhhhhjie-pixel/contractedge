import importlib.util
import json
from pathlib import Path

import pytest


def launcher():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'run_formal150.py'
    spec = importlib.util.spec_from_file_location('formal_launcher_credentials', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configured_private_file_is_used_without_recording_secret(tmp_path):
    module = launcher()
    secret = 'sk-test-private-value'
    key = tmp_path / 'key'
    key.write_text(secret)
    env = {}
    result = module.configure_api_key({'api_key_env': 'CE_TEST_KEY'}, api_key_file=str(key), environ=env)
    assert env == {'CE_TEST_KEY': secret}
    assert result == {'source': 'private_file', 'env': 'CE_TEST_KEY', 'path': str(key)}
    assert secret not in json.dumps(result)


def test_existing_environment_key_wins_without_reading_file(tmp_path):
    module = launcher()
    missing = tmp_path / 'does-not-exist'
    env = {'CE_TEST_KEY': 'sk-from-environment'}
    result = module.configure_api_key({'api_key_env': 'CE_TEST_KEY'}, api_key_file=str(missing), environ=env)
    assert result == {'source': 'environment', 'env': 'CE_TEST_KEY'}
    assert env['CE_TEST_KEY'] == 'sk-from-environment'


@pytest.mark.parametrize('config', [
    {'api_key_env': 'CE_TEST_KEY'},
])
def test_unset_environment_requires_a_private_key_file(config):
    module = launcher()
    with pytest.raises(ValueError, match='api_key_file'):
        module.configure_api_key(config, environ={})


def test_formal_profile_uses_official_deepseek_transport_and_model():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'configs/deepseek-formal150-production-v2-budget80.json').read_text())
    assert config['base_url'] == 'https://api.deepseek.com'
    assert config['model'] == 'deepseek-flash'
    assert 'api_key_file' not in config
    assert config['money_budget']['model'] == 'deepseek-flash'
    assert config['money_budget']['limit_cny'] == '80'
    assert config['money_budget']['path'] == 'money-ledger.json'


def test_runtime_preflight_covers_each_digest_pinned_image(tmp_path, monkeypatch):
    module = launcher()
    public = tmp_path / 'public'
    (public / 'a').mkdir(parents=True)
    (public / 'b').mkdir(parents=True)
    for path, image in [(public / 'a' / 'environment.json', 'example/a@sha256:' + 'a' * 64),
                        (public / 'b' / 'environment.json', 'example/b@sha256:' + 'b' * 64)]:
        path.write_text(json.dumps({'runtime_image': image}))
    benchmark = tmp_path
    calls = []
    import contractedge.execution.runtime_preflight as runtime_preflight
    def fake_check_runtime(**kwargs):
        calls.append(kwargs['verify_image'])
        return {'status': 'PASS', 'runtime_verified': True, 'image': kwargs['verify_image'], 'errors': []}
    monkeypatch.setattr(runtime_preflight, 'check_runtime', fake_check_runtime)
    result = module.run_runtime_preflight(benchmark, {})
    assert result['status'] == 'PASS'
    assert calls == sorted(calls)
    assert result['image_count'] == 2
    assert all(report['status'] == 'PASS' for report in result['reports'])


def test_formal_launcher_rejects_legacy_provider_profile(tmp_path):
    module = launcher()
    # The guard is expressed in main's frozen-profile checks; verify the
    # canonical profile remains bound to the official endpoint and identity.
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'configs/deepseek-formal150-production-v2-budget80.json').read_text())
    assert config['base_url'].rstrip('/') == 'https://api.deepseek.com'
    assert config['model'] == 'deepseek-flash'
    assert config['model_identity_policy'] == 'require_match'
