import time,uuid,re,hashlib,secrets
from .store import Conflict
from .security import password_hash,verify_password
from .config import ROLES,AGENT_PROFILES,PERMISSIONS

def uid():return uuid.uuid4().hex
def now():return int(time.time())
class Denied(Exception):
    def __init__(self,message,trace_id=None):super().__init__(message);self.trace_id=trace_id
class KnowledgeUnavailable(Denied): pass
class Invalid(Exception): pass
class Domain:
    def __init__(self,store):self.db=store
    def event_change(self,actor,action,detail,outcome='success'):
        e={'id':uid(),'actor_id':actor,'action':action,'outcome':outcome,'detail':detail,'at':now(),'trace_id':uid()}
        return ('event:'+e['id'],e,0)
    def audit(self,actor,action,detail,outcome='success'):
        c=self.event_change(actor,action,detail,outcome);self.db.transact([c]);return c[1]
    def user(self,user_id):
        u,v=self.db.get('user:'+user_id)
        if not u or not u['active']:raise Denied('帳號不存在或已停用。')
        return u
    def permission(self,user,permission):
        if permission not in user.get('permissions',[]):raise Denied('沒有此管理權限。')
    def public_user(self,u):return {k:v for k,v in u.items() if k not in ('credential',)}
    def create_user(self,actor,login,name,department,title,role,password,permissions=None,bootstrap=False):
        if bootstrap:
            if self.db.get('bootstrap')[0]:raise Invalid('管理員已初始化。')
        else:self.permission(actor,'people:manage')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',login):raise Invalid('帳號格式不正確。')
        if role not in ROLES or not all([name.strip(),department.strip(),title.strip()]):raise Invalid('請填寫姓名、部門、職稱及職務方案。')
        permissions=permissions or []
        if any(p not in PERMISSIONS for p in permissions):raise Invalid('未知權限。')
        u={'id':uid(),'login':login,'name':name.strip(),'department':department.strip(),'title':title.strip(),'role':role,'permissions':permissions,'credential':password_hash(password),'active':True,'auth_revision':1,'agent_id':None}
        changes=[('user:'+u['id'],u,0),('login:'+login,{'user_id':u['id']},0),self.event_change(actor['id'] if actor else 'bootstrap','user_created',{'user_id':u['id'],'login':login})]
        if bootstrap:changes.append(('bootstrap',{'user_id':u['id']},0))
        try:self.db.transact(changes)
        except Conflict:raise Invalid('帳號已存在或管理員已初始化。')
        return self.public_user(u)
    def login(self,login,password):
        record,_=self.db.get('login:'+login);u=self.db.get('user:'+record['user_id'])[0] if record else None
        if not u or not verify_password(password,u['credential']) or not u['active']:raise Denied('帳號或密碼錯誤。')
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
        self.db.transact([('session:'+hashlib.sha256(token.encode()).hexdigest(),{'user_id':u['id'],'revision':u['auth_revision'],'csrf':csrf,'expires':now()+8*3600},0)])
        self.audit(u['id'],'login',{})
        return token,csrf,self.public_user(u)
    def session(self,token):
        s,_=self.db.get('session:'+hashlib.sha256(token.encode()).hexdigest())
        if not s or s['expires']<=now():raise Denied('請重新登入。')
        u=self.user(s['user_id'])
        if s['revision']!=u['auth_revision']:raise Denied('權限已變更，請重新登入。')
        return u,s
    def edit_user(self,actor,user_id,patch):
        self.permission(actor,'people:manage');u,v=self.db.get('user:'+user_id)
        if not u:raise Invalid('員工不存在。')
        allowed={'name','department','title','role','permissions','active','password'}
        if set(patch)-allowed:raise Invalid('此欄位不可修改。')
        if patch.get('role',u['role']) not in ROLES:raise Invalid('未知職務方案。')
        if any(x not in PERMISSIONS for x in patch.get('permissions',u['permissions'])):raise Invalid('未知權限。')
        if actor['id']==user_id and (patch.get('active') is False or ('permissions' in patch and 'people:manage' not in patch['permissions'])):raise Invalid('不能停用自己或移除自己的管理權限。')
        before=self.public_user(u);patch=dict(patch)
        if 'password' in patch:u['credential']=password_hash(patch.pop('password'))
        u.update(patch)
        if not all(str(u[x]).strip() for x in ('name','department','title')):raise Invalid('姓名、部門及職稱不能空白。')
        u['auth_revision']+=1
        self.db.transact([('user:'+user_id,u,v),self.event_change(actor['id'],'user_edited',{'before':before,'after':self.public_user(u)})]);return self.public_user(u)
    def save_agent(self,actor,agent_id,patch):
        self.permission(actor,'agent:manage');a,v=self.db.get('agent:'+agent_id) if agent_id else (None,0)
        if agent_id and not a:raise Invalid('助理不存在。')
        a=a or {'id':uid(),'revision':0,'prompt_version':'A'}
        if set(patch)-{'name','avatar','profile','prompt_version'}:raise Invalid('此助理欄位不可修改。')
        before=dict(a);a.update(patch)
        if not a.get('name','').strip() or a.get('profile') not in AGENT_PROFILES or a['prompt_version'] not in ('A','B','C'):raise Invalid('助理名稱、規範或 Prompt 不正確。')
        if a.get('avatar','🤖') not in ('🤖','🦉','🧭','👩‍💼','👨‍💼'):raise Invalid('此版請使用內建形象。')
        a['revision']+=1;self.db.transact([('agent:'+a['id'],a,v),self.event_change(actor['id'],'agent_edited',{'before':before,'after':a})]);return a
    def assign(self,actor,user_id,agent_id):
        self.permission(actor,'agent:manage');u,v=self.db.get('user:'+user_id);a,_=self.db.get('agent:'+agent_id)
        if not u or not a:raise Invalid('員工或助理不存在。')
        before=u.get('agent_id');u['agent_id']=agent_id;u['auth_revision']+=1
        self.db.transact([('user:'+user_id,u,v),self.event_change(actor['id'],'agent_assigned',{'user_id':user_id,'before':before,'after':agent_id})])
        return self.public_user(u)
    def scope(self,u):
        a,_=self.db.get('agent:'+str(u.get('agent_id')))
        if not a:raise Denied('尚未派任助理。')
        r=ROLES[u['role']];p=AGENT_PROFILES[a['profile']]
        return {'knowledge':sorted(set(r['knowledge'])&set(p['knowledge'])),'systems':sorted(set(r['systems'])&set(p['systems'])),'stamp':f"{u['auth_revision']}:{a['id']}:{a['revision']}:itops005",'agent':a}
    def create_request(self,user_id,system,reason,key,trace):
        u=self.user(user_id);s=self.scope(u)
        if system not in s['systems']:
            self.audit(user_id,'AuthorizationDenied',{'system_id':system,'trace_id':trace},'denied');raise Denied('此系統超出共同授權。')
        from .synthetic_policy import inspect_user_text,PolicyDenied
        try:inspect_user_text(reason,u,'tool_reason')
        except PolicyDenied as ex:
            self.audit(user_id,'EgressDenied',{'reason_code':ex.code,'phase':'mcp_tool_reason','trace_id':trace},'denied');raise Denied('工具參數不符合合成環境安全規則。') from None
        if not reason.strip() or len(reason)>1000 or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise Invalid('理由或防重複識別碼不正確。')
        record_key='request:'+hashlib.sha256((user_id+':'+key).encode()).hexdigest()
        existing,_=self.db.get(record_key)
        if existing:
            if existing['system_id']!=system or existing['reason']!=reason:raise Invalid('同一防重複鍵不可更改內容。')
            return existing
        request={'id':record_key.split(':')[1],'user_id':user_id,'agent_id':s['agent']['id'],'system_id':system,'reason':reason,'idempotency_key':key,'status':'pending','created_at':now(),'trace_id':trace}
        try:self.db.transact([(record_key,request,0),self.event_change(user_id,'request_created',{'request_id':request['id']})])
        except Conflict:return self.create_request(user_id,system,reason,key,trace)
        return request
    def approve(self,u,request_id,decision):
        self.permission(u,'request:approve');key='request:'+request_id;r,v=self.db.get(key)
        if not r or decision not in ('approved','rejected'):raise Invalid('申請或決策不正確。')
        if r['user_id']==u['id']:raise Denied('不能核准自己的申請。')
        if r['status']!='pending':raise Invalid('申請已完成決策。')
        r.update(status=decision,decided_by=u['id'],decided_at=now())
        self.db.transact([(key,r,v),self.event_change(u['id'],'request_decision',{'request_id':request_id,'decision':decision})]);return r
    def new_conversation(self,u,title):
        if not title.strip() or len(title)>100:raise Invalid('名稱需為 1–100 字。')
        c={'id':uid(),'owner_id':u['id'],'title':title.strip(),'state':'active','hidden':False,'hold':False,'created_at':now(),'message_count':0}
        self.db.transact([('conversation:'+c['id'],c,0)]);return c
    def conversation(self,u,cid,audit_read=False,management=False):
        c,v=self.db.get('conversation:'+cid)
        if not c:raise Invalid('對話不存在。')
        if c['owner_id']!=u['id'] or management:
            self.permission(u,'chat:audit:read')
            if audit_read:self.audit(u['id'],'conversation_read',{'conversation_id':cid,'owner_id':c['owner_id']})
        elif c['state']!='active' or c.get('hidden'):raise Denied('此對話不在使用列表。')
        if c['state'] in ('purging','purged') or (c['state']=='deleted' and not c['hold'] and c['purge_after']<=now()):raise Denied('正文已到清除期限。')
        return c,v
    def edit_conversation(self,u,cid,action,title='',reason=''):
        c,v=self.db.get('conversation:'+cid)
        if not c or c['state'] in ('purging','purged'):raise Invalid('對話不存在或正文已清除。')
        owner=c['owner_id']==u['id']
        if action=='rename':
            if not owner:raise Denied('只有本人可重新命名。')
            if c['state']!='active' or not title.strip() or len(title)>100:raise Invalid('名稱或狀態不正確。')
            old=c['title'];c['title']=title.strip();detail={'conversation_id':cid,'before':old,'after':c['title']}
        elif action=='hide':
            if not owner:raise Denied('只能移除自己的列表項目。')
            c['hidden']=True;detail={'conversation_id':cid,'title':c['title'],'owner_id':c['owner_id']}
        else:
            self.permission(u,'chat:retain:manage')
            if not reason.strip():raise Invalid('管理操作需填寫原因。')
            if c['state']=='deleted' and not c['hold'] and c['purge_after']<=now():raise Invalid('已到清除期限，不可復原或補設保留。')
            if action=='delete':
                if c['state']!='active':raise Invalid('對話已在回收區。')
                c.update(state='deleted',deleted_at=now(),deleted_by=u['id'],purge_after=now()+30*86400)
            elif action=='restore':c.update(state='active',hidden=False);c.pop('purge_after',None)
            elif action=='hold':c['hold']=True
            elif action=='release-hold':
                c['hold']=False
                if c['state']=='deleted':c['purge_after']=now()+30*86400
            else:raise Invalid('未知對話操作。')
            detail={'conversation_id':cid,'title':c['title'],'owner_id':c['owner_id'],'reason':reason,'purge_after':c.get('purge_after')}
        self.db.transact([('conversation:'+cid,c,v),self.event_change(u['id'],'conversation_'+action,detail)]);return c
    def purge_due(self):
        count=0
        for key,c,v in self.db.scan('conversation:'):
            if c['state']!='deleted' or c['hold'] or c['purge_after']>now():continue
            # A CAS tombstone first blocks all readers/writers. Retry unfinished message removal on each run.
            c['state']='purging';self.db.transact([(key,c,v)])
        for key,c,v in self.db.scan('conversation:'):
            if c['state']!='purging':continue
            for message_key,_,mv in self.db.scan('message:'+c['id']+':'):self.db.transact([(message_key,None,mv)])
            for status_key,_,sv in self.db.scan('chat-status:'+c['id']+':'):self.db.transact([(status_key,None,sv)])
            c['state']='purged';c['purged_at']=now();self.db.transact([(key,c,v),self.event_change('system','conversation_purged',{'conversation_id':c['id'],'title':c['title'],'owner_id':c['owner_id'],'deleted_by':c['deleted_by'],'deleted_at':c['deleted_at'],'purged_at':c['purged_at']})]);count+=1
        return count
