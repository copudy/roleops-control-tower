"""Batch 002: authorized request projections and durable UI submission tracking.

The existing MCP create_access_request and request states remain authoritative.
Tracking rows contain only identity/context IDs, phase and timestamps; no reasons,
credentials or response bodies. Unknown outcomes are queried, never auto-resubmitted.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from .domain import Denied, Invalid, uid
from .config import AGENT_PROFILES
from .store import Conflict
from .runtime_release import version

UIUX_VERSION = '006-chat-status-20261103'


class UIUX:
    def __init__(self, cfg, db, domain):
        self.cfg, self.db, self.domain = cfg, db, domain

    @staticmethod
    def request_id(user_id, key):
        return hashlib.sha256((user_id + ':' + key).encode()).hexdigest()

    def visible(self, user, rid):
        if not re.fullmatch(r'[a-f0-9]{64}', rid):
            raise HTTPException(404, '找不到可查看的申請。')
        record, _ = self.db.get('request:' + rid)
        if not record or (record['user_id'] != user['id'] and 'request:approve' not in user['permissions']):
            raise HTTPException(404, '找不到可查看的申請。')
        return record

    def person(self, uid, *, include_login=False):
        if not uid:
            return None
        row, _ = self.db.get('user:' + uid)
        # Resolve only the person attached to an authorized request/event.
        identity = {'id': uid, 'current_name': row.get('name') if row else None,
                    'current_department': row.get('department') if row else None}
        if include_login:
            identity['current_login'] = row.get('login') if row else None
        return identity

    def projection(self, record):
        fields = ('id', 'system_id', 'reason', 'status', 'created_at', 'decided_at')
        result = {key: record.get(key) for key in fields}
        result['applicant'] = self.person(record.get('user_id'))
        result['reviewer'] = self.person(record.get('decided_by'))
        result['technical'] = {key: record.get(key) for key in ('trace_id', 'idempotency_key', 'agent_id')}
        return result

    def context(self, user, cid, mid):
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', mid):
            raise Invalid('原提案識別碼不正確。')
        conversation, _ = self.domain.conversation(user, cid)
        if conversation['owner_id'] != user['id']:
            raise Denied('只能查詢自己的提交結果。')
        message, _ = self.db.get('message:' + cid + ':' + mid)
        if not message or message.get('id') != mid:
            raise HTTPException(404, '找不到原提案。')
        return self.request_id(user['id'], mid)

    def lookup(self, user, cid, mid):
        rid = self.context(user, cid, mid)
        record, _ = self.db.get('request:' + rid)
        if record:
            if record['user_id'] != user['id']:
                raise Denied('申請身分不符。')
            return {'state': 'completed', 'request': self.projection(record)}
        attempt, _ = self.db.get('uiux-submit:' + user['id'] + ':' + mid)
        if attempt:
            if attempt['conversation_id'] != cid:
                raise Denied('提交脈絡不符。')
            return {'state': attempt['phase'], 'request': None}
        return {'state': 'not_submitted', 'request': None}

    async def submit(self, user, body, dispatch):
        # Called only after all original g5 proposal/authorization/source checks.
        mid, cid = body['message_id'], body['conversation_id']
        rid = self.request_id(user['id'], mid)
        record, _ = self.db.get('request:' + rid)
        if record:
            if record['system_id'] != body['system_id'] or record['reason'] != body['reason']:
                raise Invalid('原防重複鍵的申請內容已固定，請查看原申請。')
            return record
        key = 'uiux-submit:' + user['id'] + ':' + mid
        attempt, revision = self.db.get(key)
        if attempt:
            if attempt['conversation_id'] != cid or attempt['request_id'] != rid:
                raise Denied('提交脈絡不符。')
            return JSONResponse({'dispatch_status': 'uncertain', 'submission_state': attempt['phase'],
                                 'idempotency_key': mid, 'message': '原提交結果查核中，請查詢同一筆申請。'}, status_code=202)
        attempt = {'owner_id': user['id'], 'conversation_id': cid, 'message_id': mid,
                   'request_id': rid, 'phase': 'processing', 'started_at': int(time.time())}
        try:
            self.db.transact([(key, attempt, 0)])
        except Conflict:
            return JSONResponse({'dispatch_status': 'uncertain', 'submission_state': 'processing',
                                 'idempotency_key': mid, 'message': '同一提交正在處理，請查詢狀態。'}, status_code=202)
        try:
            result = await dispatch()
            saved, _ = self.db.get('request:' + rid)
            phase = 'completed' if saved and saved.get('id') == rid else 'unknown'
            self._finish(key, phase)
            if phase == 'completed':
                return saved
            return JSONResponse({'dispatch_status': 'uncertain', 'submission_state': 'unknown',
                                 'idempotency_key': mid, 'message': '提交結果尚不明確，請查詢原申請。'}, status_code=202)
        except BaseException:
            # Includes worker cancellation or MCP error after a possible write.
            self._finish(key, 'unknown')
            raise

    def _finish(self, key, phase):
        try:
            row, rev = self.db.get(key)
            if row:
                row.update(phase=phase, updated_at=int(time.time()))
                self.db.transact([(key, row, rev)])
        except Exception:
            # A processing marker still blocks resubmission; lookup reads request first.
            pass

    def _cursor(self, payload):
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        signature = hmac.new(self.cfg.mcp_secret.encode(), raw, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' + signature

    def history(self, user, rid, cursor, page_size):
        self.visible(user, rid)
        if not 1 <= page_size <= 50:
            raise Invalid('每頁筆數需為 1–50。')
        after = None
        if cursor:
            if len(cursor)>2048:raise Invalid('歷程分頁識別過長。')
            try:
                encoded, signature = cursor.split('.')
                raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
                expected = hmac.new(self.cfg.mcp_secret.encode(), raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, signature):
                    raise ValueError()
                payload = json.loads(raw)
                if payload['rid'] != rid or payload['viewer'] != user['id']:
                    raise ValueError()
                after = tuple(payload['after'])
            except Exception:
                raise Invalid('歷程分頁識別已失效，請重新載入。') from None
        events = [e for _, e, _ in self.db.scan('event:')
                  if e.get('detail', {}).get('request_id') == rid
                  and e.get('action') in ('request_created', 'request_decision')]
        events.sort(key=lambda e: ((e.get('at') or 0), e['id']))
        if after:
            events = [e for e in events if ((e.get('at') or 0), e['id']) > after]
        selected = events[:page_size]
        next_cursor = None
        if len(events) > page_size:
            last = selected[-1]
            next_cursor = self._cursor({'rid': rid, 'viewer': user['id'], 'after': [(last.get('at') or 0), last['id']]})
        projected = [{'id': e['id'], 'at': e.get('at'), 'actor': self.person(e.get('actor_id')),
                      'action': e['action'], 'outcome': e.get('outcome'),
                      'decision': e.get('detail', {}).get('decision'), 'trace_id': e.get('trace_id')}
                     for e in selected]
        return {'items': projected, 'next_cursor': next_cursor, 'complete': next_cursor is None,
                'note': '僅呈現已保存的建立與決策事件；同秒以事件編號排序。'}

    def save_companion_agent(self, actor, aid, patch):
        # Same original agent authorization and validation, plus a whitelisted skin.
        # Agent fields and skin are committed together with the existing audit event.
        self.domain.permission(actor, 'agent:manage')
        if not isinstance(patch, dict) or set(patch) - {'name','avatar','profile','prompt_version','companion_skin'}:
            raise Invalid('此助理欄位不可修改。')
        row, revision = self.db.get('agent:' + aid) if aid else (None, 0)
        if aid and not row:
            raise Invalid('助理不存在。')
        row = row or {'id': uid(), 'revision': 0, 'prompt_version': 'A'}
        before = dict(row)
        row = dict(row, **patch)
        if (not isinstance(row.get('name'), str) or not row['name'].strip()
                or len(row['name']) > 100
                or not isinstance(row.get('profile'), str) or row['profile'] not in AGENT_PROFILES
                or row.get('prompt_version') not in ('A','B','C')
                or row.get('avatar','🤖') not in ('🤖','🦉','🧭','👩‍💼','👨‍💼')
                or row.get('companion_skin', 'sprout') not in ('sprout','owl','cloud','fox','star','turtle')):
            raise Invalid('請選擇有效的助理名稱、規範、Prompt 與預設形象。')
        row['revision'] += 1
        self.db.transact([('agent:' + row['id'], row, revision),
                         self.domain.event_change(actor['id'], 'agent_edited', {'before': before, 'after': row})])
        return row

    def companion_preference(self, user):
        aid = user.get('agent_id')
        agent, _ = self.db.get('agent:' + str(aid))
        if not agent:
            return {'agent_id': None, 'nickname': '', 'display_name': None}
        pref, _ = self.db.get('uiux-companion:' + user['id'])
        nickname = pref.get('nickname', '') if pref and pref.get('agent_id') == aid else ''
        return {'agent_id': aid, 'nickname': nickname, 'display_name': nickname or agent['name']}

    def name_companion(self, user, body):
        # Personal nickname only. No employee-controlled changes to the shared agent.
        if not isinstance(body, dict) or set(body) != {'agent_id', 'nickname'}:
            raise Invalid('請提供自己的助理編號與暱稱。')
        aid = user.get('agent_id')
        agent, agent_version = self.db.get('agent:' + str(aid))
        if not aid or not agent or body.get('agent_id') != aid:
            raise Denied('助理派任已變更，請重新整理後再編輯。')
        nickname = body['nickname']
        if (not isinstance(nickname, str) or len(nickname) > 30
                or any(ord(c) < 32 or ord(c) == 127 for c in nickname)):
            raise Invalid('助理暱稱最多 30 個字，請使用單行文字。')
        nickname = nickname.strip()
        key = 'uiux-companion:' + user['id']
        before, revision = self.db.get(key)
        # Read the authoritative user again so reassignment/revocation races cannot
        # commit a nickname against an obsolete assignment. CAS ties it to auth.
        current, user_version = self.db.get('user:' + user['id'])
        if (not current or not current.get('active') or current.get('agent_id') != aid
                or current.get('auth_revision') != user.get('auth_revision')):
            raise Denied('登入或助理派任已變更，請重新登入。')
        pref = {'owner_id': user['id'], 'agent_id': aid, 'nickname': nickname}
        self.db.transact([(key, pref, revision), ('user:' + user['id'], current, user_version),
                          ('agent:' + aid, agent, agent_version),
                          self.domain.event_change(user['id'], 'companion_named',
                             {'agent_id': aid, 'before': before.get('nickname', '') if before else '', 'after': nickname})])
        return {'agent_id': aid, 'nickname': nickname, 'display_name': nickname or agent['name']}

    def directory(self, user, kind, q='', department='', active='all', sort='login', page=1, page_size=12):
        self.domain.permission(user, 'people:manage' if kind == 'users' else 'agent:manage')
        if not isinstance(q, str) or len(q) > 100 or len(department) > 100 or active not in ('all','true','false') or sort not in ('login','name','department') or page < 1 or not 1 <= page_size <= 24:
            raise Invalid('目錄查詢參數不正確。')
        rows = [self.domain.public_user(row) if kind == 'users' else dict(row) for _,row,_ in self.db.scan('user:' if kind == 'users' else 'agent:')]
        departments = sorted({row.get('department','') for row in rows if row.get('department')}) if kind == 'users' else []
        search = q.strip().casefold()
        rows = [row for row in rows if (not search or search in ' '.join(str(row.get(k,'')) for k in ('name','login','department','title','profile')).casefold())
                and (not department or row.get('department') == department)
                and (kind != 'users' or active == 'all' or bool(row.get('active')) == (active == 'true'))]
        key = sort if kind == 'users' else 'name'
        rows.sort(key=lambda row:(str(row.get(key,'')).casefold(),str(row.get('login','')).casefold(),row['id']))
        total = len(rows);pages = max(1, (total+page_size-1)//page_size);page=min(page,pages)
        rows=rows[(page-1)*page_size:page*page_size]
        if kind == 'users':
            for row in rows:
                agent,_=self.db.get('agent:'+str(row.get('agent_id')))
                row['assigned_agent'] = {k:agent.get(k) for k in ('id','name','companion_skin')} if agent else None
        return {'items':rows,'total':total,'page':page,'pages':pages,'page_size':page_size,'departments':departments}

    def register(self, app, auth):
        @app.get('/api/uiux/admin/users')
        def user_directory(req: Request, q: str='', department: str='', active: str='all', sort: str='login', page: int=1, page_size: int=12):
            return self.directory(auth(req),'users',q,department,active,sort,page,page_size)

        @app.get('/api/uiux/admin/agents')
        def agent_directory(req: Request, q: str='', page: int=1, page_size: int=6):
            return self.directory(auth(req),'agents',q,page=page,page_size=page_size)

        @app.get('/api/uiux/companion-preference')
        def read_companion_preference(req: Request):
            return self.companion_preference(auth(req))

        @app.patch('/api/uiux/companion-preference')
        async def write_companion_preference(req: Request):
            user = auth(req, True)
            return self.name_companion(user, await req.json())

        @app.post('/api/uiux/agents')
        async def create_companion_agent(req: Request):
            user = auth(req, True)
            return self.save_companion_agent(user, None, await req.json())

        @app.patch('/api/uiux/agents/{aid}')
        async def edit_companion_agent(aid: str, req: Request):
            user = auth(req, True)
            return self.save_companion_agent(user, aid, await req.json())

        @app.get('/api/uiux/client-config')
        def client_config(req: Request):
            auth(req)
            return {'submit_timeout_ms': int((self.cfg.timeout + 5) * 1000), 'read_timeout_ms': 15000,
                    'source': 'MCP timeout setting plus 5 seconds for API/result recovery'}

        @app.get('/api/uiux/requests')
        def request_list(req: Request, approvals: bool = False):
            user = auth(req)
            if approvals:
                self.domain.permission(user, 'request:approve')
            rows = [record for _, record, _ in self.db.scan('request:')
                    if approvals or record['user_id'] == user['id']]
            rows.sort(key=lambda item: (item.get('created_at') or 0, item['id']), reverse=True)
            return [self.projection(record) for record in rows]

        @app.get('/api/uiux/requests/{rid}')
        def detail(rid: str, req: Request):
            return self.projection(self.visible(auth(req), rid))

        @app.get('/api/uiux/requests/{rid}/history')
        def history(rid: str, req: Request, cursor: str = '', page_size: int = 20):
            return self.history(auth(req), rid, cursor, page_size)

        @app.get('/api/uiux/request-result')
        def result(req: Request, conversation_id: str, message_id: str):
            return self.lookup(auth(req), conversation_id, message_id)

        @app.get('/api/uiux/system')
        def system(req: Request):
            self.domain.permission(auth(req), 'ops:read')
            region = os.environ.get('AWS_REGION') or None
            return {'mode': self.cfg.mode, 'region': region, 'region_source': 'AWS_REGION' if region else '未提供',
                    'release': version(), 'uiux_revision': UIUX_VERSION,
                    'source': '伺服器執行設定與程式版本'}
