import os,time,json,hashlib,secrets
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import JSONResponse,FileResponse
from fastapi.staticfiles import StaticFiles
from .config import Settings,ROLES,AGENT_PROFILES,PERMISSIONS
from .store import Store,Conflict
from .domain import Domain,Denied,KnowledgeUnavailable,Invalid,now
from .knowledge import Knowledge
from .agent import Agent
from .runtime_release import version
from .uiux_views import UIUX

def create_app(cfg=None):
    cfg=cfg or Settings();cfg.validate();db=Store(cfg);domain=Domain(db);kb=Knowledge(cfg,db);agent=Agent(cfg,domain,kb)
    app=FastAPI(title='RoleOps Control Tower',docs_url=None,redoc_url=None);app.state.domain=domain;app.state.kb=kb;app.state.agent=agent
    uiux=UIUX(cfg,db,domain);app.state.uiux=uiux
    assets=Path(__file__).parent.parent/'static';app.mount('/static',StaticFiles(directory=assets),name='static')
    @app.exception_handler(KnowledgeUnavailable)
    async def knowledge_unavailable(req,e):
        from .observability import emit
        from .domain import uid
        from .observability import generation_error_code
        trace=e.trace_id or uid();emit(cfg,'app','knowledge','failure',trace,{'TaskFailure':1},error_type='KnowledgeUnavailable',error_code=generation_error_code(getattr(e,'reason_code','')))
        category=getattr(e,'category','source_unavailable')
        allowed={'provider_unavailable','provider_request_rejected','grounding_incomplete','response_contract_rejected'}
        if category not in allowed:category='source_unavailable'
        from .chat_status import failure_view
        status=getattr(e,'conversation_status',None) or failure_view('source_unavailable' if category=='source_unavailable' else generation_error_code(getattr(e,'reason_code','')),trace)
        return JSONResponse({'detail':status['text'],'trace_id':trace,'category':category,'error_code':generation_error_code(getattr(e,'reason_code','')) if category!='source_unavailable' else 'source_unavailable','conversation_status':status},status_code=503)
    @app.exception_handler(Denied)
    async def denied(req,e):
        from .observability import emit
        from .domain import uid
        trace=e.trace_id or uid();emit(cfg,'app','authorization','denied',trace,{'AuthorizationDenied':1})
        return JSONResponse({'detail':str(e),'trace_id':trace},status_code=403)
    @app.exception_handler(Invalid)
    async def invalid(req,e):return JSONResponse({'detail':str(e)},status_code=400)
    @app.exception_handler(Conflict)
    async def conflict(req,e):return JSONResponse({'detail':'資料同時變更，請重新讀取後重試。'},status_code=409)
    @app.exception_handler(KeyError)
    async def missing(req,e):return JSONResponse({'detail':'請求缺少必要欄位。'},status_code=422)
    @app.exception_handler(TypeError)
    async def malformed(req,e):return JSONResponse({'detail':'請求欄位型別不正確。'},status_code=422)
    @app.exception_handler(ValueError)
    async def value(req,e):return JSONResponse({'detail':str(e)},status_code=400)
    @app.middleware('http')
    async def guards(req,call_next):
        if cfg.public_origin and not secrets.compare_digest(req.headers.get('x-roleops-origin','').encode('utf-8'),cfg.origin_secret.encode('utf-8')):
            return JSONResponse({'detail':'入口驗證失敗。'},status_code=403,headers={'Cache-Control':'no-store'})
        length=req.headers.get('content-length','0')
        if length.isdigit() and int(length)>65536:return JSONResponse({'detail':'請求過大。'},status_code=413)
        if req.method not in ('GET','HEAD','OPTIONS'):
            origin=req.headers.get('origin')
            if origin:
                if cfg.public_origin:
                    valid=origin==cfg.public_origin
                else:
                    valid=__import__('urllib.parse',fromlist=['urlparse']).urlparse(origin).netloc==req.headers.get('host')
                if not valid:return JSONResponse({'detail':'來源不符合。'},status_code=403)
        request_user=None
        try:
            identity,_=domain.session(req.cookies.get('roleops_session',''))
            request_user=identity['id']
        except Denied:pass
        response=await call_next(req);
        if request_user:response.headers['X-RoleOps-User']=request_user
        if cfg.public_origin:response.headers['Strict-Transport-Security']='max-age=86400'
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin';response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'";return response
    def auth(req,write=False):
        try:u,s=domain.session(req.cookies.get('roleops_session',''))
        except Denied:raise HTTPException(401,'請重新登入。')
        if write and not secrets.compare_digest(req.headers.get('x-csrf-token',''),s['csrf']):raise Denied('操作驗證失敗。')
        return u
    @app.get('/')
    def index():return FileResponse(assets/'index.html')
    @app.get('/api/status')
    def status():return {'initialized':bool(db.get('bootstrap')[0]),'mode':cfg.mode,'release':version(),'extension_revision':'010-integrated-guidance-20261103','browser_bootstrap':cfg.mode=='local'}
    @app.post('/api/bootstrap')
    async def bootstrap(req:Request):
        if cfg.mode!='local':raise Denied('AWS 模式請在 EC2 使用 bootstrap-admin。')
        b=await req.json();return domain.create_user(None,b['login'],'平台管理員','平台管理','管理員','supervisor',b['password'],list(PERMISSIONS),bootstrap=True)
    @app.post('/api/auth/login')
    async def login(req:Request):
        b=await req.json();identity=(req.headers.get('x-forwarded-for','').split(',')[-1].strip() if cfg.public_origin else (req.client.host if req.client else 'unknown'));key='login-rate:'+hashlib.sha256(identity.encode()).hexdigest();r,v=db.get(key);r=r or {'count':0,'until':now()+300}
        if r['until']<=now():r={'count':0,'until':now()+300}
        if r['count']>=10:raise HTTPException(429,'登入嘗試過多，請五分鐘後重試。')
        try:token,csrf,u=domain.login(b.get('login',''),b.get('password',''))
        except Denied:r['count']+=1;db.transact([(key,r,v)]);raise HTTPException(401,'帳號或密碼錯誤。')
        if v:db.transact([(key,None,v)])
        response=JSONResponse({'user':u,'csrf':csrf});response.set_cookie('roleops_session',token,httponly=True,samesite='strict',secure=bool(cfg.public_origin),max_age=8*3600,path='/');return response
    @app.get('/api/me')
    def me(req:Request):
        u=auth(req);_,s=domain.session(req.cookies['roleops_session']);a=db.get('agent:'+str(u.get('agent_id')))[0];return {'user':domain.public_user(u),'agent':a,'csrf':s['csrf']}
    @app.post('/api/auth/logout')
    def logout(req:Request):
        u=auth(req,True);key='session:'+hashlib.sha256(req.cookies['roleops_session'].encode()).hexdigest();_,v=db.get(key);db.transact([(key,None,v)]);response=JSONResponse({'ok':True});response.delete_cookie('roleops_session',secure=bool(cfg.public_origin),httponly=True,samesite='strict',path='/');return response
    @app.get('/api/config')
    def config(req:Request):auth(req);return {'roles':ROLES,'profiles':AGENT_PROFILES,'permissions':PERMISSIONS}
    @app.get('/api/users')
    def users(req:Request):u=auth(req);domain.permission(u,'people:manage');return [domain.public_user(x) for _,x,_ in db.scan('user:')]
    @app.post('/api/users')
    async def create_user(req:Request):u=auth(req,True);b=await req.json();return domain.create_user(u,b['login'],b['name'],b['department'],b['title'],b['role'],b['password'],b.get('permissions',[]))
    @app.patch('/api/users/{user_id}')
    async def edit_user(user_id:str,req:Request):return domain.edit_user(auth(req,True),user_id,await req.json())
    @app.get('/api/agents')
    def agents(req:Request):u=auth(req);domain.permission(u,'agent:manage');return [x for _,x,_ in db.scan('agent:')]
    @app.post('/api/agents')
    async def create_agent(req:Request):return domain.save_agent(auth(req,True),None,await req.json())
    @app.patch('/api/agents/{aid}')
    async def edit_agent(aid:str,req:Request):return domain.save_agent(auth(req,True),aid,await req.json())
    @app.put('/api/users/{user_id}/agent-assignment')
    async def assign(user_id:str,req:Request):return domain.assign(auth(req,True),user_id,(await req.json())['agent_id'])
    @app.get('/api/conversations')
    def conversations(req:Request,management:bool=False):
        u=auth(req)
        if management:
            domain.permission(u, 'chat:audit:read')
        records = sorted(
            [c for _, c, _ in db.scan('conversation:')
             if management or (c['owner_id'] == u['id'] and c['state'] == 'active' and not c['hidden'])],
            key=lambda c: c.get('updated_at') or c.get('created_at') or 0,
            reverse=True,
        )
        if not management:
            return records
        owners = {}
        for record in records:
            uid = record.get('owner_id')
            if uid not in owners:
                owners[uid] = uiux.person(uid, include_login=True)
        return [dict(record, owner=owners[record.get('owner_id')]) for record in records]
    @app.post('/api/conversations')
    async def new_conversation(req:Request):return domain.new_conversation(auth(req,True),(await req.json()).get('title','新對話'))
    @app.patch('/api/conversations/{cid}')
    async def edit_conversation(cid:str,req:Request):b=await req.json();return domain.edit_conversation(auth(req,True),cid,b['action'],b.get('title',''),b.get('reason',''))
    @app.get('/api/conversations/{cid}/messages')
    def messages(cid:str,req:Request,management:bool=False):
        u=auth(req);c,_=domain.conversation(u,cid,audit_read=True,management=management);items=sorted([m for _,m,_ in db.scan('message:'+cid+':')],key=lambda m:m.get('sequence',m['at']))
        if c['owner_id']==u['id'] and not management:
            try:stamp=domain.scope(u)['stamp']
            except Denied:stamp=None
            for m in items:
                if m['stamp']!=stamp or m['generation']!=kb.status().get('generation'):m.update(question='此段歷史因授權或文件版本變更而隱藏。',answer='',sources=[],proposal=None)
        return items
    @app.get('/api/conversations/{cid}/activity')
    def conversation_activity(cid:str,req:Request,management:bool=False):
        u=auth(req);c,_=domain.conversation(u,cid,audit_read=True,management=management)
        rows=sorted([x for _,x,_ in db.scan('chat-status:'+cid+':')],key=lambda x:(x['at'],x['id']))[-100:]
        if c['owner_id']==u['id'] and not management:
            try:stamp=domain.scope(u)['stamp']
            except Denied:stamp=None
            generation=kb.status().get('generation')
            for row in rows:
                if row['stamp']!=stamp or row['generation']!=generation:
                    row.update(question='此段歷史因授權或文件版本變更而隱藏。',answer='')
                    row['conversation_status']={'schema':1,'kind':'system_status','state':'hidden','text':'此段歷史因授權或文件版本變更而隱藏。','retry':'none'}
        return rows
    @app.post('/api/conversations/{cid}/messages')
    async def chat(cid:str,req:Request):return await agent.reply(auth(req,True),cid,(await req.json())['question'])
    @app.get('/api/requests')
    def requests(req:Request,approvals:bool=False):
        u=auth(req)
        if approvals:domain.permission(u,'request:approve')
        return [r for _,r,_ in db.scan('request:') if approvals or r['user_id']==u['id']]
    @app.post('/api/requests')
    async def request_create(req:Request):
        u=auth(req,True)
        try:b=await req.json()
        except (ValueError,TypeError):raise Invalid('申請格式不正確。') from None
        if not isinstance(b,dict) or not isinstance(b.get('system_id'),str) or not b['system_id'].strip():raise Invalid('請指定申請系統。')
        s=domain.scope(u)
        if b['system_id'] not in s['systems']:
            event=domain.audit(u['id'],'AuthorizationDenied',{'system_id':b['system_id']},'denied');raise Denied('此系統超出共同授權。',trace_id=event['trace_id'])
        from .synthetic_policy import inspect_user_text,PolicyDenied,SAFE_REPLY
        if b.get('confirmed') is not True:raise Invalid('請先確認申請內容。')
        if any(not isinstance(b.get(k),str) or not b[k] for k in ('reason','conversation_id','message_id','idempotency_key')):raise Invalid('申請參數不完整。')
        if len(b['reason'])>1000:raise Invalid('使用理由過長。')
        try:inspect_user_text(b['reason'],u,'tool_reason')
        except PolicyDenied as ex:
            event=domain.audit(u['id'],'EgressDenied',{'reason_code':ex.code,'phase':'tool_reason'},'denied');raise Denied(SAFE_REPLY,trace_id=event['trace_id'])
        c,_=domain.conversation(u,b['conversation_id'])
        m,_=db.get('message:'+b['conversation_id']+':'+b['message_id'])
        if c['owner_id']!=u['id'] or c['state']!='active' or c['hold'] or not m or not m.get('proposal'):raise Denied('沒有可確認的有效提案。')
        if m['stamp']!=s['stamp'] or m['proposal']['system_id']!=b['system_id'] or b['idempotency_key']!=m['id']:raise Denied('提案或授權已異動，請重新提問。')
        try:kb.assert_current([{'document':x} for x in m['sources']],m['generation'])
        except KnowledgeUnavailable:
            event=domain.audit(u['id'],'StaleProposalDenied',{'reason_code':'source_version_changed','message_id':m['id'],'proposal_generation':m['generation'],'current_generation':kb.status().get('generation')},'denied')
            raise Denied('來源版本已異動，請重新提問並確認新提案。',trace_id=event['trace_id']) from None
        result=await uiux.submit(u,b,lambda:agent.call_tool(u,b['system_id'],b['reason'],b['idempotency_key']))
        from .chat_status import request_view
        if isinstance(result,JSONResponse):
            payload=json.loads(result.body);payload['conversation_status']=request_view(payload)
            return JSONResponse(payload,status_code=result.status_code,headers={k:v for k,v in result.headers.items() if k not in ('content-length','content-type')})
        return dict(result,conversation_status=request_view(result))
    @app.post('/api/approvals/{rid}')
    async def approval(rid:str,req:Request):
        result=domain.approve(auth(req,True),rid,(await req.json())['decision'])
        from .chat_status import request_view
        return dict(result,conversation_status=request_view(result))
    @app.get('/api/operations')
    def operations(req:Request):
        u = auth(req)
        domain.permission(u, 'ops:read')
        events = sorted((e for _, e, _ in db.scan('event:')),
                        key=lambda e: e.get('at') or 0, reverse=True)[:100]
        actors = {}
        for event in events:
            uid = event.get('actor_id')
            if uid not in actors:
                actors[uid] = uiux.person(uid, include_login=True)
        return {'index': kb.status(),
                'events': [dict(e, actor=actors[e.get('actor_id')]) for e in events],
                'mode': cfg.mode}
    @app.exception_handler(Exception)
    async def unexpected(req,e):
        from .observability import emit
        from .domain import uid
        trace=uid();emit(cfg,'app','request','failure',trace,{'TaskFailure':1},error_type=type(e).__name__)
        return JSONResponse({'detail':'服務發生錯誤，請依追蹤碼查閱維運紀錄。','trace_id':trace},status_code=500)
    @app.get('/healthz')
    def health():
        from .health import snapshot
        return snapshot(cfg,db,kb)
    uiux.register(app,auth)
    from .extension import mount
    mount(app,auth,domain,kb,cfg)
    return app

app=create_app() if os.getenv('ROLEOPS_TEST')!='true' else None
