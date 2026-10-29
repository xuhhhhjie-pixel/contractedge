"""Reconstruct the exact parent authority without touching a frozen method."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--system-root',type=Path,required=True);ap.add_argument('--destination',type=Path,required=True);a=ap.parse_args()
 bundle=Path(__file__).resolve().parent;manifest=json.loads((bundle/'bundle.json').read_text());source=a.system_root.resolve(strict=True);dest=a.destination.absolute()
 if dest.exists() or dest.is_symlink() or dest.resolve().is_relative_to(source) or source.is_relative_to(dest.resolve()):raise ValueError('Use a fresh separate parent directory; never modify or replace the frozen source')
 for name,expected in manifest['files'].items():
  if Path(name).name!=name or sha(bundle/name)!=expected:raise ValueError('Parent bundle changed: '+name)
 files={p.relative_to(source/'src').as_posix():sha(p) for p in sorted((source/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
 digest=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 if digest!=manifest['method_source_sha256'] or sha(source/'scripts/fixed_panel.py')!=manifest['original_fixed_panel_sha256']:raise ValueError('Source or original parent authority differs from the qualified input')
 dest.mkdir(parents=True)
 for name in ['src','scripts','tests']:
  if (source/name).is_dir():shutil.copytree(source/name,dest/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 for name in ['pyproject.toml','pytest.ini','requirements-dev.lock']:
  if (source/name).is_file():shutil.copy2(source/name,dest/name)
 for option in [['--check'],[]]:subprocess.run(['git','apply',*option,str(bundle/'fixed_panel.patch')],cwd=dest,check=True,capture_output=True)
 for name in ['financial_continuation.py','prepare_financial_panel.py','run_financial_panel.py']:shutil.copy2(bundle/name,dest/'scripts'/name)
 (dest/'tests').mkdir(exist_ok=True);shutil.copy2(bundle/'test_financial_continuation.py',dest/'tests/test_financial_continuation.py')
 if sha(dest/'scripts/fixed_panel.py')!=manifest['qualified_fixed_panel_sha256']:raise ValueError('Reconstructed authority bytes differ')
 print(json.dumps({'status':'EXACT_PARENT_RECONSTRUCTION_PASS','method_source_sha256':digest,'destination':str(dest),'model_calls':0,'source_modified':False,'formal_ready':False}))

if __name__=='__main__':main()
