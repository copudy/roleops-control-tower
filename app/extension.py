import os,json,hashlib,time
from pathlib import Path
from .domain import Denied,KnowledgeUnavailable,Invalid
from . import showcase_content

def source_preview(domain,kb,user,cid,mid,position):
    c,_=domain.conversation(user,cid)
    if c['owner_id']!=user['id'] or c['hidden'] or c['state']!='active':raise Denied('只能查閱自己目前可用的對話來源。')
    message,_=domain.db.get('message:'+cid+':'+mid)
    scope=domain.scope(domain.user(user['id']))
    if not message or message['conversation_id']!=cid:raise Invalid('訊息不存在。')
    generation=kb.status().get('generation')
    if message['stamp']!=scope['stamp'] or message['generation']!=generation:raise Denied('授權或文件版本已異動，請重新提問。')
    sources=message.get('sources',[])
    if not 0<=position<len(sources):raise Invalid('來源識別不正確。')
    source=sources[position]
    actual=next((s for s in kb.sources() if s[0]==source['key']),None)
    if not actual:raise KnowledgeUnavailable('來源已移除。')
    doc=kb.parse(*actual)
    if (doc['sha256']!=source['sha256'] or doc.get('version_id')!=source.get('version_id')) or not kb.eligible(doc,user,scope):raise Denied('來源已更新或不在目前授權內。')
    kb.assert_current([{'document':doc}],generation)
    # Recheck identity/scope after source IO; do not inherit stale rights.
    if domain.scope(domain.user(user['id']))['stamp']!=scope['stamp']:raise Denied('授權已異動。')
    if not kb.eligible(doc,domain.user(user['id']),scope):raise Denied('目前身分已無來源查閱權。')
    domain.audit(user['id'],'source_read',{'document_id':doc['document_id'],'conversation_id':cid,'message_id':mid})
    return {'document_id':doc['document_id'],'policy_version':doc['policy_version'],'body':doc['body'],'sha256':doc['sha256']}

def content(user,domain):
    domain.permission(user,'ops:read')
    try:data,folder=showcase_content.load(os.getenv('ROLEOPS_SHOWCASE_ROOT','/opt/roleops/showcase'))
    except (OSError,ValueError,KeyError,TypeError):raise KnowledgeUnavailable('工程展示資料尚未發布或驗證失敗。')
    return data,folder

def snapshot(cfg,clock=time.time):
    path=cfg.root/'selfcheck.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8'));age=clock()-data['checked_at']
        if not isinstance(data['checks'],list) or age<0 or age>900:raise ValueError('stale')
        return dict(data,fresh=True,age_seconds=round(age))
    except (OSError,ValueError,KeyError,TypeError):
        return {'fresh':False,'checked_at':None,'checks':[],'message':'沒有有效自檢資料，不能判定健康；請查排程與 CloudWatch。'}

def mount(app,auth,domain,kb,cfg):
    from fastapi import Request
    from fastapi.responses import FileResponse
    # Request annotation must be global for FastAPI's annotation resolution.
    globals()['Request']=Request
    @app.get('/api/conversations/{cid}/sources/{mid}/{position}')
    def source(cid:str,mid:str,position:int,req:Request):return source_preview(domain,kb,auth(req),cid,mid,position)
    @app.get('/api/engineering')
    def engineering(req:Request):
        data,_=content(auth(req),domain);return data
    @app.get('/api/engineering/assets/{asset_id}')
    def asset(asset_id:str,req:Request):
        data,folder=content(auth(req),domain)
        if asset_id not in data['assets']:raise Invalid('附件不存在。')
        a=data['assets'][asset_id];p=showcase_content.asset_path(folder,a['path'])
        return FileResponse(p,filename=p.name,content_disposition_type='attachment')
    @app.get('/api/selfcheck')
    def selfcheck(req:Request):domain.permission(auth(req),'ops:read');return snapshot(cfg)
    from . import maintenance_dashboard as dashboard

    def dashboard_access(req):
        user = auth(req)
        domain.permission(user, 'ops:read')
        domain.permission(user, 'people:manage')

    @app.get('/api/engineering/monitoring')
    def monitoring(req: Request, window: str = '1h'):
        dashboard_access(req)
        if window not in dashboard.WINDOWS:
            raise Invalid('不支援的觀測區間。')
        return dashboard.snapshot(cfg, window)

    @app.get('/api/engineering/incidents')
    def incidents(req: Request):
        dashboard_access(req)
        return {'incidents': dashboard.CASES}
