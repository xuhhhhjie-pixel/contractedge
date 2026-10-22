import re
from contractedge.execution.probe_specs import registered_programs,KPT_CONFIGPATH_V2_GO


def test_v3_keeps_old_program_and_registers_all_variants_with_valid_go_literals():
    registry=registered_programs()
    assert registry['kpt-configpath-v2'][0]==KPT_CONFIGPATH_V2_GO
    assert 'kpt-configpath-v3' in registry
    program,checks=registry['kpt-configpath-v3']
    assert len(checks)==38
    for name in ['mutator','validator','control','mutator_selector','mutator_exclusion']:
        assert name+'.identity_annotations_cleaned' in checks
        assert name+'.resource_inventory' in checks
    assert '\n      exclusions:' not in program
    assert '\n      exclude:' in program
    assert 'filepath.Walk(' in program and 'len(identities)==3' in program
    assert 'identity_annotation_count' in program
    # Go double quoted strings cannot contain an unescaped source newline.
    strings=re.findall(r'"(?:[^"\\]|\\.)*"',program)
    assert not any('\n' in literal for literal in strings), 'Probe contains invalid multiline Go string'
