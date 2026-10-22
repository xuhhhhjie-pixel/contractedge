from contractedge.execution.probe_specs import registered_programs


def test_inventory_enumerates_extensionless_package_resource_without_changing_v3():
    registry=registered_programs()
    old,old_checks=registry['kpt-configpath-v3']
    new,checks=registry['kpt-configpath-v4']
    assert 'filepath.Base(path)!="Kptfile"' in new
    assert 'filepath.Base(path)!="Kptfile"' not in old
    assert checks==old_checks and len(checks)==38
    assert 'kpt-configpath-v4' in new and 'kpt-configpath-v3' not in new
