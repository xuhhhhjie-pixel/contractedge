from contractedge.execution.probes import probe_command


def test_versioned_alertmanager_probe_retains_its_public_offline_environment():
    old=probe_command('alertmanager-reload-v1')
    new=probe_command('alertmanager-reload-v2')
    assert old==new
    assert probe_command('alertmanager-reload-v3')==old
    assert 'GOPROXY=off' in new[-1]
    assert 'setup/go.sum' not in new[-1]
    assert 'setup/go.sum' in probe_command('kpt-configpath-v4')[-1]
