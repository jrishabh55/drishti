#!/usr/bin/env python3
"""Install/start or inspect the private read-only historical research worker.

No credentials leave the host. Existing API image and persistent volume are
reused without restarting the application. Application database files are mounted read-only; SQLite sidecar coordination
requires writable containing directories.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

REMOTE = r'''
import base64,hashlib,io,json,os,pathlib,re,sqlite3,subprocess,sys,tarfile
request=json.load(sys.stdin)
api='drishti-qzkexq-api-1'
profile=request.get('profile','weekend')
if profile not in ('weekend','long-daily'):raise RuntimeError('invalid worker profile')
long_daily=profile=='long-daily'
if request['action']=='upgrade' and not long_daily:raise RuntimeError('only daily worker upgrades are supported')
name='drishti-long-daily-history-20261031' if long_daily else 'drishti-weekend-research-20261031'
cpu='0.25' if long_daily else '0.5'
memory='512m' if long_daily else '1100m'
memory_swap='768m' if long_daily else '1400m'
module='research.long_daily' if long_daily else 'research.cli'
base='/home/appuser/.tradingagents/research-archives/'+('long-daily-20261031-v2' if long_daily else 'weekend-20261031')
def run(args,**kw):
    return subprocess.run(args,check=True,capture_output=True,text=True,**kw).stdout
if request['action']=='status':
    info=json.loads(run(['docker','inspect',name]))[0]
    print(json.dumps({'name':name,'state':info['State']['Status'],'exit_code':info['State']['ExitCode'],
                      'oom_killed':info['State']['OOMKilled'],'restarts':info['RestartCount']}))
    # Logs contain only sanitized numeric progress/status, never provider errors.
    print(run(['docker','logs','--tail','5',name]))
    sys.exit(0)
info=json.loads(run(['docker','inspect',api]))[0]
image=info['Image']
mount=next(m for m in info['Mounts'] if m['Destination']=='/home/appuser/.tradingagents')
volume=pathlib.Path(mount['Source'])
archive=volume/'research-archives'; archive.mkdir(mode=0o700,exist_ok=True)
os.chmod(archive,0o700);os.chown(archive,1000,1000)
payload=base64.b64decode(request['tar'])
digest=hashlib.sha256(payload).hexdigest()
tools=volume/'.research-tools'/digest
tools.mkdir(parents=True,exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as tf:
    for member in tf.getmembers():
        parts=pathlib.PurePosixPath(member.name).parts
        if len(parts)!=2 or parts[0]!='research' or not parts[1].endswith('.py') or not member.isfile():
            raise RuntimeError('invalid source artifact')
        target=tools.joinpath(*parts);target.parent.mkdir(exist_ok=True)
        target.write_bytes(tf.extractfile(member).read());target.chmod(0o444)
existing=subprocess.run(['docker','inspect',name],capture_output=True,text=True)
replacement=None
dataset_engine=image+'/research:'+digest
if existing.returncode==0:
    old=json.loads(existing.stdout)[0]
    old_source=old['Config'].get('Labels',{}).get('research.source')
    if old_source==digest:
        print(json.dumps({'existing':True,'state':old['State']['Status'],'source':digest}));sys.exit(0)
    if request['action']!='upgrade':raise RuntimeError('existing worker has another source version; inspect before replacing')
    command=old['Config'].get('Cmd',[])
    if old['Image']!=image or 'research.long_daily' not in command or '--base' not in command or command[command.index('--base')+1]!=base:
        raise RuntimeError('existing worker dataset/image is incompatible')
    if not old_source or not re.fullmatch('[0-9a-f]{64}',old_source):raise RuntimeError('invalid prior worker identity')
    dataset_engine=command[command.index('--engine-version')+1]
    replacement=name+'-prior-'+old_source[:12]
elif request['action']=='upgrade':raise RuntimeError('no existing daily worker to upgrade')
env=dict(v.split('=',1) for v in info['Config']['Env'])
keys=['WEBAPP_KEY_ENCRYPTION_SECRET','KITE_DB_PATH','SCANNER_DB_PATH','DESK_DB_PATH']
values={k:env[k] for k in keys}
values.update(KITE_ORDERS_ENABLED='0',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',
              OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',
              PYTHONPATH='/home/appuser/.tradingagents/.research-tools/'+digest+':/home/appuser/app')
if any('\n' in v or '\r' in v for v in values.values()):raise RuntimeError('invalid environment')
args=['docker','run','-d','--name',name,'--label','research.source='+digest,
      '--restart=on-failure:5','--cpus='+cpu,'--memory='+memory,'--memory-swap='+memory_swap,
      '--pids-limit=64','--user','1000:1000','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
      '--cap-drop=ALL','--security-opt=no-new-privileges','--network=bridge',
      '--log-opt','max-size=5m','--log-opt','max-file=2',
      '--volumes-from',api+':rw','--mount','type=bind,src='+str(archive)+',dst=/home/appuser/.tradingagents/research-archives',
      '--env-file','/dev/stdin','--entrypoint','python',image,
      '-m',module,'--base',base,
      '--engine-version',dataset_engine,'--watch']
if long_daily:args+=['--parent-base','/home/appuser/.tradingagents/research-archives/weekend-20261031',
                    '--runtime-version',image+'/research:'+digest]
# Protect each application database file even though SQLite needs writable sidecars.
for key in ('KITE_DB_PATH','SCANNER_DB_PATH','DESK_DB_PATH'):
    destination=pathlib.Path(values[key])
    relative=destination.relative_to('/home/appuser/.tradingagents')
    source=volume/relative
    if not source.is_file():raise RuntimeError('database path unavailable')
    args[2:2]=['--mount','type=bind,src='+str(source)+',dst='+str(destination)+',readonly']
args[2:2]=['--mount','type=bind,src='+str(tools)+',dst=/home/appuser/.tradingagents/.research-tools/'+digest+',readonly']
if replacement:
    dataset_path=volume/pathlib.Path(base).relative_to('/home/appuser/.tradingagents')
    manifests=list(dataset_path.glob('*/kite/manifest.sqlite'))+list(dataset_path.glob('*/yahoo/manifest.sqlite'))
    if len(manifests)!=2:raise RuntimeError('expected two existing provider archives')
    for manifest in manifests:
        with sqlite3.connect(manifest.as_uri()+'?mode=ro',uri=True) as c:
            dataset=json.loads(c.execute("SELECT v FROM meta WHERE k='config'").fetchone()[0])
        if dataset.get('engine_version')!=dataset_engine or dataset.get('provider')!=manifest.parent.name or dataset.get('daily_start')!='2000-01-01' or dataset.get('intervals')!=['1d']:
            raise RuntimeError('existing daily archive contract is incompatible')
        if hashlib.sha256(dataset['owner'].encode()).hexdigest()!=manifest.parent.parent.name:
            raise RuntimeError('existing archive owner scope is inconsistent')
    run(['docker','update','--restart=no',name])
    run(['docker','stop','-t','15',name])
    run(['docker','rename',name,replacement])
container=run(args,input=''.join(k+'='+v+'\n' for k,v in values.items())).strip()
print(json.dumps({'container':container,'name':name,'source':digest,'image':image,
                  'cpu_limit':float(cpu),'memory_mib':int(memory[:-1]),'orders_enabled':False}))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['launch','status','upgrade'])
    p.add_argument('--host',default='codeation-dev')
    p.add_argument('--profile',choices=['weekend','long-daily'],default='weekend')
    args=p.parse_args()
    request={'action':args.action,'profile':args.profile}
    if args.action in ('launch','upgrade'):
        root=Path(__file__).resolve().parents[1]/'apps'/'research'
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w:gz') as tar:
            for path in sorted(root.glob('*.py')):
                data=path.read_bytes()
                entry=tarfile.TarInfo('research/'+path.name)
                entry.size=len(data);entry.mode=0o444;entry.mtime=0
                tar.addfile(entry,io.BytesIO(data))
        # Gzip's header timestamp isn't used for the immutable source identity.
        import gzip
        payload=gzip.compress(gzip.decompress(buf.getvalue()),mtime=0)
        request['tar']=base64.b64encode(payload).decode()
    result=subprocess.run(['ssh',args.host,'python3 -c '+shlex.quote(REMOTE)],
                          input=json.dumps(request),text=True,capture_output=True)
    if result.stdout:print(result.stdout,end='')
    if result.returncode:
        # Docker/environment exceptions can contain secrets: do not echo stderr.
        raise SystemExit('Research operation failed; inspect sanitized status on the host.')

if __name__=='__main__':main()
