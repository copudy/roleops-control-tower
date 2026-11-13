"""Private, schema-bound backup; restore only to an inactive isolated target.

SHA checks integrity, not independent authenticity. The bucket, identity and
source manifest remain trusted deployment boundaries. No index is promoted.
"""
import hashlib,json,os,re,time
from pathlib import Path
from dataclasses import replace
from .store import Store
from .runtime_release import version

TARGET='roleops-verify-restore-test'
SOURCE='roleops-verify-main'
BUCKET='roleops-verify-REPLACE_WITH_APPROVED_ACCOUNT_ID-us-east-1'
MARKER='restore:state'
ALLOWED=('user:','login:','agent:','request:','event:')
EVENT_FIELDS={'id','actor_id','action','outcome','at','trace_id'}
DETAIL_FIELDS={'conversation_id','owner_id','request_id','system_id','decision','reason_code','phase','input_sha256','generation','message_id','proposal_generation','current_generation','deleted_by','deleted_at','purged_at','purge_after','trace_id'}

def digest(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def allowed(key):return key=='bootstrap' or key.startswith(ALLOWED)
def safe_row(key,value):
    if not isinstance(value,dict):raise ValueError('Invalid backup record')
    if key.startswith('event:'):
        value={k:v for k,v in value.items() if k in EVENT_FIELDS or k=='detail'}
        value['detail']={k:v for k,v in value.get('detail',{}).items() if k in DETAIL_FIELDS}
    return value
def private_write(path,data):
    # Exclusive creation rejects existing files, including links.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def label_path(cfg,label):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',label):raise ValueError('Invalid backup label')
    folder=Path(cfg.root)/'backups';folder.mkdir(mode=0o700,exist_ok=True)
    if folder.is_symlink():raise ValueError('Unsafe backup directory')
    folder.chmod(0o700);return folder/(label+'.json')

def backup(cfg,db,kb,label):
    path=label_path(cfg,label);rows=[];excluded=0
    for k,v,n in db.scan():
        if allowed(k):rows.append({'key':k,'value':safe_row(k,v),'version':n})
        else:excluded+=1
    manifest=Path(__file__).parent.parent/'RELEASE.json'
    data={'schema':2,'release':version(),'source_table':cfg.table,'source_bucket':cfg.bucket,
          'source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
          'created_at':int(time.time()),'rows':sorted(rows,key=lambda x:x['key']),
          'conversation_content_included':False,'index_restored':False,
          'index_snapshot_observation':kb.status(),'excluded_rows':excluded}
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True).encode();h=hashlib.sha256(raw).hexdigest()
    private_write(path,raw);private_write(path.with_suffix('.sha256'),h.encode())
    receipt={'file':str(path),'sha256':h,'schema':2,'release':version(),'rows':len(rows),'excluded_rows':excluded,'conversation_content_included':False,'index_restored':False}
    if cfg.mode=='aws':
        import boto3
        s3=boto3.client('s3',region_name=cfg.region);objects=[]
        for f in (path,path.with_suffix('.sha256')):
            obj=s3.put_object(Bucket=cfg.bucket,Key='backups/'+f.name,Body=f.read_bytes(),ServerSideEncryption='AES256',IfNoneMatch='*')
            objects.append({'key':'backups/'+f.name,'version_id':obj.get('VersionId'),'sse':obj.get('ServerSideEncryption')})
        receipt['objects']=objects
    return receipt

def validate_backup(raw,expected):
    if not re.fullmatch('[a-f0-9]{64}',expected) or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Backup SHA-256 mismatch')
    if len(raw)>8_000_000:raise ValueError('Backup too large')
    data=json.loads(raw)
    if data.get('schema')!=2 or data.get('conversation_content_included') is not False or data.get('index_restored') is not False:raise ValueError('Unapproved backup schema')
    rows=data.get('rows');seen=set()
    if not isinstance(rows,list) or len(rows)>5000:raise ValueError('Invalid backup rows')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'key','value','version'}:raise ValueError('Invalid backup row')
        k=row['key']
        if not isinstance(k,str) or not allowed(k) or k in seen:raise ValueError('Excluded or duplicate backup key')
        seen.add(k)
        if not isinstance(row['version'],int) or row['version']<1:raise ValueError('Invalid backup version')
        if safe_row(k,row['value'])!=row['value']:raise ValueError('Backup contains excluded audit content')
    return data

def assert_target(cfg,args,identity):
    if not args.isolated or args.target_table!=TARGET or cfg.table!=SOURCE or cfg.bucket!=BUCKET or cfg.region!='us-east-1':raise ValueError('Restore requires exact isolated source and target')
    if identity.get('Account')!='REPLACE_WITH_APPROVED_ACCOUNT_ID' or not (identity.get('Arn','')=='arn:aws:iam::REPLACE_WITH_APPROVED_ACCOUNT_ID:user/roleops-verifier' or identity.get('Arn','').startswith('arn:aws:sts::REPLACE_WITH_APPROVED_ACCOUNT_ID:assumed-role/roleops-verify-ec2-role/')):raise ValueError('Restore identity not approved')

def restore_rows(target,data,backup_sha):
    expected={x['key']:dict(x['value'],active=False) if x['key'].startswith('user:') else x['value'] for x in data['rows']}
    # Prevent stale policies and accounts from becoming an active application.
    state={'state':'inactive','backup_sha256':backup_sha,'rows':len(expected),'rows_sha256':digest(expected),'index_restored':False,'accounts_disabled':True}
    existing=target.scan()
    if existing:
        actual={k:v for k,v,_ in existing};marker=actual.pop(MARKER,None)
        if marker==state and actual==expected:return {'restored':len(expected),'isolated':True,'idempotent':True,**state}
        raise ValueError('Restore target is not empty or exact completed owned restore')
    # Reserve the target with CAS; another cooperating restore cannot start.
    target.transact([(MARKER,dict(state,state='loading'),0)])
    wrote=0
    try:
        if any(k!=MARKER for k,_,_ in target.scan()):raise ValueError('Concurrent target write')
        items=list(expected.items())
        for off in range(0,len(items),80):
            batch=items[off:off+80];target.transact([(k,v,0) for k,v in batch]);wrote+=len(batch)
        actual={k:v for k,v,_ in target.scan() if k!=MARKER}
        if actual!=expected:raise ValueError('Restore verification mismatch')
        _,v=target.get(MARKER);target.transact([(MARKER,state,v)])
    except Exception:
        marker,v=target.get(MARKER)
        if marker and marker.get('backup_sha256')==backup_sha:target.transact([(MARKER,dict(state,state='failed',written_rows=wrote),v)])
        raise
    return {'restored':len(expected),'isolated':True,'idempotent':False,**state}

def restore(cfg,db,kb,args):
    if cfg.mode=='aws' and not args.verify_only:
        import boto3
        assert_target(cfg,args,boto3.client('sts',region_name=cfg.region).get_caller_identity())
    elif not args.verify_only and not args.isolated:raise ValueError('Restore test requires --isolated')
    path=label_path(cfg,args.backup)
    if cfg.mode=='aws':
        import boto3
        s3=boto3.client('s3',region_name=cfg.region)
        raw=s3.get_object(Bucket=cfg.bucket,Key='backups/'+path.name)['Body'].read(8_000_001)
        expected=s3.get_object(Bucket=cfg.bucket,Key='backups/'+path.with_suffix('.sha256').name)['Body'].read(65).decode().strip()
    else:raw=path.read_bytes();expected=path.with_suffix('.sha256').read_text().strip()
    data=validate_backup(raw,expected)
    if args.verify_only:return {'verified':True,'rows':len(data['rows']),'conversation_content_included':False,'index_restored':False}
    if cfg.mode=='aws' and (data.get('source_table')!=cfg.table or data.get('source_bucket')!=cfg.bucket):raise ValueError('Backup source binding mismatch')
    target=Store(replace(cfg,table=args.target_table) if cfg.mode=='aws' else replace(cfg,root=cfg.root/'restore-test'))
    return restore_rows(target,data,expected)
