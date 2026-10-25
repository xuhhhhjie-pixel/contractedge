"""Bounded public API and configuration-consumer lexical entry hints."""
import ast
import re
from pathlib import Path,PurePosixPath
from contractedge.retrieval.candidates import literal_identifiers


def probe_entrypoint_hints(snapshot, probe_descriptions, max_files=128,max_bytes=4*1024*1024):
    hints=[];paths=[];inspected=0;used=0;truncated=False;root=Path(snapshot.source_root)
    for probe in probe_descriptions:
        for api in probe.get('api_entrypoints',[]):
            package=api.get('package','');symbol=api.get('symbol','');python=api.get('language')=='python'
            if (not package or PurePosixPath(package).is_absolute() or any(p in {'.','..',''} for p in package.split('/'))
                    or not re.fullmatch(r'[A-Za-z_]\w*',symbol)):
                raise ValueError('Invalid registered public probe API descriptor')
            inventory=sorted(p for p in snapshot.file_digests
                if ((p.startswith(package+'/') and p.endswith('.py') and '/tests/' not in p and not Path(p).name.startswith('test_')) if python
                    else (str(PurePosixPath(p).parent)==package and p.endswith('.go') and not p.endswith('_test.go'))))
            for relative in inventory:
                if inspected>=max_files:truncated=True;break
                path=(root/relative).resolve();path.relative_to(root.resolve());size=path.stat().st_size
                if (root/relative).is_symlink() or size>1024*1024:truncated=True;continue
                if used+size>max_bytes:truncated=True;break
                inspected+=1;used+=size;text=path.read_text(errors='replace')
                if python:
                    try:found=any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name==symbol for n in ast.walk(ast.parse(text)))
                    except SyntaxError:found=False
                else:
                    found=bool(re.search(r'(?m)^\s*(?:type\s+'+re.escape(symbol)+r'\b|func\s+(?:\([^\n)]*\)\s*)?'+re.escape(symbol)+r'\s*\()',text))
                if not found:continue
                if relative not in paths:paths.append(relative)
                producer=probe.get('producer_manifest_sha256');origin='METHOD_PUBLIC_PRODUCER_API:' if producer else 'METHOD_PROBE_PUBLIC_API:'
                hints.append({'kind':'path','value':relative,'raw_text':f'Public API {package}.{symbol}',
                    'origin_ref':origin+(producer or probe['sha256'])+':'+symbol})
    return hints,paths,{'inspected_files':inspected,'inspected_bytes':used,'truncated':truncated,'located_paths':paths,
        'basis':'Declarations of public APIs used by registered public producers or probe programs; not repair targets or causal edges'}


def configuration_consumer_hints(snapshot, seed_paths, requirement, max_files=128, max_bytes=4*1024*1024):
    """Locate exact public requirement identifiers shared with seed configs."""
    root=Path(snapshot.source_root);terms=literal_identifiers(requirement);config_terms=set()
    for name in seed_paths[:24]:
        if not name.endswith(('.yaml','.yml','.json','.tpl')) or name not in snapshot.file_digests:continue
        p=root/name
        if p.is_symlink() or p.stat().st_size>1024*1024:continue
        text=p.read_text(errors='replace')
        config_terms.update(t for t in terms if len(t)>=4 and re.search(r'(?<!\w)'+re.escape(t)+r'(?!\w)',text,re.I))
    paths=[];hints=[];files=used=0;truncated=False
    for name in sorted(snapshot.file_digests):
        if not config_terms:break
        if not name.endswith(('.go','.py','.ts','.js','.java','.c','.h')) or any(p in {'tests','test','vendor','node_modules'} for p in Path(name).parts) or name.endswith('_test.go') or Path(name).name.startswith('test_'):continue
        p=root/name
        if p.is_symlink():continue
        size=p.stat().st_size
        if files>=max_files or used+size>max_bytes:truncated=True;break
        files+=1;used+=size;text=p.read_text(errors='replace')
        found=[t for t in sorted(config_terms) if re.search(r'(?<!\w)'+re.escape(t)+r'(?!\w)',text,re.I)]
        if found:
            paths.append(name);hints.append({'kind':'path','value':name,'raw_text':'Public configuration identifiers: '+', '.join(found),
                'origin_ref':'METHOD_PUBLIC_CONFIG_CONSUMER:'+','.join(found)})
            if len(paths)>=8:truncated=True;break
    return hints,paths,{'inspected_files':files,'inspected_bytes':used,'truncated':truncated,'identifiers':sorted(config_terms),
        'basis':'Bounded exact identifier sharing with public configuration; not a verified causal edge or edit grant'}
