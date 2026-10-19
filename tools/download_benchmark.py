"""Download checksum-bound ContractEdge release packages using Python stdlib."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,os,shutil,tarfile,tempfile,urllib.request

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
    return h.hexdigest()

def safe_name(name):
    if not isinstance(name,str) or not name or Path(name).name!=name or '\\' in name or name in {'.','..'}:
        raise ValueError('Invalid release filename')
    return name

def fetch(url,target,expected,size):
    if target.exists() and target.stat().st_size==size and sha(target)==expected:
        print('Already verified:',target.name,flush=True);return
    partial=target.with_name(target.name+'.download')
    offset=partial.stat().st_size if partial.exists() else 0
    if offset>size:partial.unlink();offset=0
    headers={'User-Agent':'ContractEdge-download/1'}
    if offset:headers['Range']='bytes='+str(offset)+'-'
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=120) as response:
        append=response.status==206 and response.headers.get('Content-Range','').startswith('bytes '+str(offset)+'-')
        with partial.open('ab' if append else 'wb') as f:
            shutil.copyfileobj(response,f,4*1024**2)
    if partial.stat().st_size!=size or sha(partial)!=expected:
        raise ValueError('Download size/SHA256 mismatch: '+target.name)
    partial.replace(target);print('Verified:',target.name,flush=True)

def unpack(archive,destination,root_name):
    final=destination/safe_name(root_name)
    if final.exists():
        print('Extraction directory exists; preserved:',final,flush=True);return
    if not hasattr(tarfile,'data_filter'):raise RuntimeError('Safe extraction requires Python with tarfile.data_filter; Python3.12+ recommended')
    temporary=Path(tempfile.mkdtemp(prefix='.contractedge-extract-',dir=destination))
    try:
        with tarfile.open(archive,'r:gz') as tf:
            for member in tf:
                path=PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=root_name:
                    raise ValueError('Archive entry escapes expected root')
                tf.extract(member,temporary,filter='data')
        root=temporary/root_name
        for line in (root/'SHA256SUMS').read_text().splitlines():
            digest,name=line.split('  ',1);file=(root/name).resolve(strict=True)
            if not file.is_relative_to(root.resolve()) or file.is_symlink() or sha(file)!=digest:
                raise ValueError('Extracted payload identity mismatch: '+name)
        root.rename(final);print('Extracted and verified:',final,flush=True)
    finally:shutil.rmtree(temporary)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--class',dest='category',required=True,choices=['R1','R2','R3','R4','R5','all'])
    parser.add_argument('--dest',type=Path,default=Path('datasets'))
    parser.add_argument('--repo',default='xuhhhhjie-pixel/contractedge')
    parser.add_argument('--tag',default='benchmark-v1-20261019')
    parser.add_argument('--extract',action='store_true')
    args=parser.parse_args();destination=args.dest.resolve();destination.mkdir(parents=True,exist_ok=True)
    if args.repo.count('/')!=1 or any(x in args.repo for x in ['..','?','#','\\']):raise ValueError('Invalid GitHub repository')
    safe_name(args.tag)
    base='https://github.com/'+args.repo+'/releases/download/'+args.tag+'/'
    with urllib.request.urlopen(base+'BENCHMARK_ASSETS.json',timeout=60) as response:manifest=json.load(response)
    if manifest['schema_version']!='contractedge.github_release_assets.v1' or manifest['status']!='COMPLETE':raise ValueError('Release asset manifest is incomplete')
    selected=['R1','R2','R3','R4','R5'] if args.category=='all' else [args.category]
    for category in selected:
        row=manifest['classes'][category];archive=destination/safe_name(row['archive_name'])
        if archive.exists() and archive.stat().st_size==row['archive_bytes'] and sha(archive)==row['archive_sha256']:
            print('Archive already verified:',archive,flush=True)
        else:
            parts=[]
            for part in row['parts']:
                name=safe_name(part['name']);target=destination/name
                fetch(base+name,target,part['sha256'],part['bytes']);parts.append(target)
            if len(parts)==1 and parts[0]==archive:
                if sha(archive)!=row['archive_sha256']:raise ValueError('Archive SHA256 mismatch')
            else:
                combined=archive.with_name(archive.name+'.assembling')
                with combined.open('wb') as output:
                    for part in parts:
                        with part.open('rb') as f:shutil.copyfileobj(f,output,4*1024**2)
                if combined.stat().st_size!=row['archive_bytes'] or sha(combined)!=row['archive_sha256']:
                    raise ValueError('Combined archive SHA256 mismatch')
                combined.replace(archive)
                for part in parts:part.unlink()
        if args.extract:unpack(archive,destination,row['archive_root'])
    return 0

if __name__=='__main__':raise SystemExit(main())
