"""Create deterministic review ZIP from an exact clean commit; never publish."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'custom_components/ha_entity_renamer/'
ROOT_FILES = {'hacs.json', 'ha-entity-renamer.js', 'LICENSE', 'NOTICE', 'README.md', 'MIGRATION.md', 'CHANGELOG.md', 'SECURITY.md', 'banner.png', 'docs/screenshots/card-queue-light.jpg', 'docs/screenshots/card-queue-dark.jpg', '.github/workflows/hacs.yml', '.github/workflows/hassfest.yml'}

def git(*args): return subprocess.check_output(['git', '-C', str(ROOT), *args])

def main():
    p=argparse.ArgumentParser();p.add_argument('--commit',required=True);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    commit=git('rev-parse',a.commit+'^{commit}').decode().strip()
    if commit!=a.commit or git('rev-parse','HEAD').decode().strip()!=commit or git('status','--porcelain'):
        p.error('requires exact clean HEAD commit')
    output=a.output.expanduser().absolute()
    if (output.exists() or any(x.is_symlink() for x in (output, *output.parents))
            or not output.parent.is_dir() or ROOT in output.parents):
        p.error('output must be a new file outside source repo')
    entries=git('ls-tree','-rz','--full-tree',commit,'--',PREFIX,*sorted(ROOT_FILES)).split(b'\0');files={}
    for entry in filter(None,entries):
        metadata,path=entry.split(b'\t',1);mode,kind,oid=metadata.decode().split();name=path.decode()
        if mode!='100644' or kind!='blob':raise ValueError('unsupported source object '+name)
        files[name]=git('cat-file','blob',oid)
    if not ROOT_FILES.issubset(files) or PREFIX+'manifest.json' not in files or PREFIX+'www/ha-entity-renamer.js' not in files:
        raise ValueError('required file missing')
    inventory={'source_commit':commit,'files':{k:hashlib.sha256(v).hexdigest() for k,v in sorted(files.items())}}
    files['SOURCE-LOCK.json']=(json.dumps(inventory,indent=2,sort_keys=True)+'\n').encode()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,data)
    with output.open('xb') as f:f.write(buf.getvalue())
    print(json.dumps({'commit':commit,'files':len(files),'sha256':hashlib.sha256(buf.getvalue()).hexdigest()}))
if __name__=='__main__':main()
