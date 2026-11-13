import io,json,unittest
from unittest.mock import patch
from urllib.error import HTTPError
import test_demo_lines as fixtures
from app.original_generation import GenerationFailure
from app.synthetic_policy import PolicyDenied
from app.chat_status import request_view

PARAPHRASE='設備兩次停機的客訴，請整理可呈核方案及需要補充的證據。'
CRM='請幫我建立 CRM 待核准申請，理由是要調閱客戶資料以進行聯繫。'
class IntegratedLineTests(unittest.TestCase):
    setUp=fixtures.DemoFlowTests.setUp
    tearDown=fixtures.DemoFlowTests.tearDown
    switch_to_support=fixtures.DemoFlowTests.switch_to_support
    send=fixtures.DemoFlowTests.send
    def new_chat(self):self.cid=self.client.post('/api/conversations',json={'title':'完整換句'}).json()['id']
    def model_error(self,code=400):
        def fail(request,**kw):
            self.provider_calls+=1
            raise HTTPError(request.full_url,code,'Synthetic',{},io.BytesIO(b'{"error":{"code":"json_validate_failed","type":"invalid_request_error"}}'))
        return patch('app.generation.urllib.request.urlopen',fail)
    def test_customer_case_followup_and_fresh_paraphrase_survive_model_rejection(self):
        self.switch_to_support('客服')
        with self.model_error():
            for q in (fixtures.SUPPORT,fixtures.SUMMARY):
                r=self.send(q);self.assertEqual(r['grounding']['answer_origin'],'verified_source_fallback');self.assertTrue(r['grounding']['coverage']['complete'])
            self.new_chat();r=self.send(PARAPHRASE)
        self.assertIn('主管',r['answer']);self.assertIn('缺口',r['answer']);self.assertTrue(r['sources']);self.assertIsNone(r['proposal'])
        self.assertEqual(r['conversation_status']['state'],'saved');self.assertEqual(len(self.client.get('/api/conversations/'+self.cid+'/messages').json()),1)
    def test_rate_limit_allows_only_complete_reviewed_document_answer(self):
        with self.model_error(429):r=self.send(fixtures.CURRENT)
        self.assertEqual(r['grounding']['answer_origin'],'verified_source_fallback');self.assertIn('檢查範本',r['answer']);self.assertIsNone(r['proposal'])
    def test_current_manual_has_fillable_five_fields_and_next_step(self):
        r=self.send(fixtures.CURRENT)
        for text in ('設備型號：＿＿','手冊版本與適用性：＿＿','本次告警代碼：＿＿','現場狀態：＿＿','執行人員資格：＿＿','下一步','聯絡建議','文件依據'):self.assertIn(text,r['answer'])
        self.assertEqual(r['grounding']['presentation']['template_values'],'unconfirmed')
    def test_archived_manual_reply_is_connected_current_guidance(self):
        r=self.send(fixtures.OLD)
        for text in ('2024.1 已封存','2026.1','檢查範本','現行文件依據','授權'):self.assertIn(text,r['answer'])
        self.assertEqual(self.provider_calls,0);self.assertTrue(all(s['policy_version']=='2026.1' for s in r['sources']))
    def test_qualification_has_explicit_contact_suggestion_and_source(self):
        r=self.send('人員資格尚未確認，應該由誰判讀？')
        self.assertIn('IT／維運主管',r['answer']);self.assertIn('當班負責人',r['answer']);self.assertIn('聯絡建議',r['answer']);self.assertIn('授權紀錄',r['answer'])
    def test_unknown_numeric_question_does_not_get_reviewed_fallback(self):
        with self.model_error():r=self.send('告警再現，維修費用多少？')
        self.assertEqual(r['grounding']['status'],'insufficient');self.assertEqual(self.provider_calls,0)
    def test_changed_scope_blocks_recovery_after_provider_failure(self):
        self.switch_to_support()
        def fail(*args):
            d=self.app.state.domain;u=next(u for _,u,_ in d.db.scan('user:') if u['login']=='staff');admin=next(u for _,u,_ in d.db.scan('user:') if u['login']=='admin')
            d.edit_user(admin,u['id'],{'department':'HR'});raise GenerationFailure('provider_http_400')
        with patch('app.generation.invoke',fail),patch('app.guidance_render.verified_case_fallback',side_effect=AssertionError('MUST_NOT_RECOVER')):
            r=self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':fixtures.SUPPORT})
        self.assertTrue(r.json()['rejected']);self.assertEqual(self.app.state.domain.db.scan('message:'+self.cid+':'),[])
    def test_nonreviewed_failure_is_status_activity_not_factual_history(self):
        self.switch_to_support()
        # CRM policy guidance remains outside the bounded maintenance/support recovery.
        with self.model_error(429):r=self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':'CRM 有哪些條件？'})
        self.assertEqual(r.status_code,503,r.text);s=r.json()['conversation_status'];self.assertEqual(s['code'],'model_rate_limited');self.assertTrue(s['status_saved']);self.assertFalse(s['message_saved'])
        rows=self.client.get('/api/conversations/'+self.cid+'/activity').json();self.assertEqual(len(rows),1);self.assertTrue(rows[0]['exclude_from_generation']);self.assertEqual(rows[0]['sources'],[]);self.assertIsNone(rows[0]['proposal'])
        self.assertEqual(self.client.get('/api/conversations/'+self.cid+'/messages').json(),[])
    def test_native_crm_proposal_failure_never_falls_back_or_creates_request(self):
        self.switch_to_support()
        with self.model_error(429):r=self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':CRM})
        self.assertEqual(r.status_code,503);self.assertEqual(r.json()['conversation_status']['request_state'],'not_created');self.assertEqual(self.app.state.domain.db.scan('request:'),[])
    def test_activity_read_rechecks_owner_and_management_permission(self):
        self.switch_to_support()
        with self.model_error(429):self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':CRM})
        from fastapi.testclient import TestClient
        d=self.app.state.domain;admin=next(u for _,u,_ in d.db.scan('user:') if u['login']=='admin')
        d.create_user(admin,'viewer','合成其他員工','IT','IT專員','it','SyntheticPassword123!',[])
        other=TestClient(self.app);other.post('/api/auth/login',json={'login':'viewer','password':'SyntheticPassword123!'})
        try:
            self.assertEqual(other.get('/api/conversations/'+self.cid+'/activity').status_code,403)
            other.post('/api/auth/login',json={'login':'admin','password':'SyntheticPassword123!'})
            self.assertEqual(other.get('/api/conversations/'+self.cid+'/activity?management=true').status_code,200)
        finally:other.close()
    def test_status_activity_hidden_after_assignment_change(self):
        self.switch_to_support()
        with self.model_error(429):self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':CRM})
        d=self.app.state.domain;u=next(u for _,u,_ in d.db.scan('user:') if u['login']=='staff');admin=next(u for _,u,_ in d.db.scan('user:') if u['login']=='admin');d.edit_user(admin,u['id'],{'title':'客服人員'})
        login=self.client.post('/api/auth/login',json={'login':'staff','password':'SyntheticPassword123!'});self.client.headers['x-csrf-token']=login.json()['csrf']
        row=self.client.get('/api/conversations/'+self.cid+'/activity').json()[0];self.assertEqual(row['conversation_status']['state'],'hidden');self.assertNotIn('CRM',row['question'])
    def test_no_status_activity_is_sent_back_to_generation(self):
        self.switch_to_support()
        with self.model_error(429):self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':CRM})
        original=__import__('app.generation',fromlist=['generate']).generate;seen=[]
        async def generate(*args,**kw):seen.extend(args[3]);return await original(*args,**kw)
        with patch('app.generation.generate',generate):self.send(fixtures.SUPPORT)
        self.assertEqual(seen,[])
    def test_denial_is_saved_and_bypasses_retrieval_model_and_tool(self):
        with patch.object(self.app.state.kb,'retrieve',side_effect=AssertionError('NO_RETRIEVAL')):r=self.send(fixtures.DENIED)
        self.assertEqual(r['conversation_status']['kind'],'access_denied');self.assertEqual(self.provider_calls,0);self.assertEqual(self.app.state.domain.db.scan('request:'),[])
    def test_request_status_unknown_requires_original_lookup(self):
        self.assertEqual(request_view({'dispatch_status':'uncertain'})['retry'],'lookup_original')
        self.assertNotIn('未建立',request_view({'dispatch_status':'uncertain'})['text'])
    def test_customer_can_confirm_native_crm_then_manager_approve_same_id(self):
        self.switch_to_support('客服');d=self.app.state.domain
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return json.dumps({'choices':[{'finish_reason':'tool_calls','message':{'content':'','tool_calls':[{'id':'synthetic-call','type':'function','function':{'name':'create_access_request','arguments':json.dumps({'system_id':'crm','reason':'要調閱客戶資料以進行聯繫'})}}]}}],'usage':{'prompt_tokens':12,'completion_tokens':6}}).encode()
        with patch('app.generation.urllib.request.urlopen',return_value=Response()):r=self.send(CRM)
        self.assertEqual(r['proposal']['system_id'],'crm');self.assertEqual(r['conversation_status']['request_state'],'awaiting_confirmation');self.assertEqual(d.db.scan('request:'),[])
        body={'system_id':'crm','reason':r['proposal']['reason'],'confirmed':True,'conversation_id':self.cid,'message_id':r['id'],'idempotency_key':r['id']}
        denied=self.client.post('/api/requests',json=dict(body,confirmed=False));self.assertEqual(denied.status_code,400)
        async def dispatch(u,system,reason,key):return d.create_request(u['id'],system,reason,key,'synthetic-trace')
        with patch.object(self.app.state.agent,'call_tool',dispatch):
            first=self.client.post('/api/requests',json=body);second=self.client.post('/api/requests',json=body)
        self.assertEqual(first.status_code,200,first.text);rid=first.json()['id'];self.assertEqual(second.json()['id'],rid);self.assertEqual(first.json()['conversation_status']['state'],'pending')
        from fastapi.testclient import TestClient
        manager=TestClient(self.app);login=manager.post('/api/auth/login',json={'login':'admin','password':'SyntheticPassword123!'});manager.headers['x-csrf-token']=login.json()['csrf']
        try:
            approved=manager.post('/api/approvals/'+rid,json={'decision':'approved'});self.assertEqual(approved.status_code,200,approved.text)
            self.assertEqual(approved.json()['conversation_status']['state'],'approved')
            own=self.client.get('/api/requests').json();same=next(x for x in own if x['id']==rid);self.assertEqual(same['status'],'approved');self.assertEqual(len(d.db.scan('request:')),1)
        finally:manager.close()
    def test_customer_cannot_request_finance_with_crm_permission(self):
        self.switch_to_support();r=self.client.post('/api/requests',json={'system_id':'finance_reporting'})
        self.assertEqual(r.status_code,403);self.assertEqual(self.app.state.domain.db.scan('request:'),[])

    def test_status_activity_follows_existing_purge_and_hold_rules(self):
        self.switch_to_support()
        with self.model_error(429):self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':CRM})
        d=self.app.state.domain;c,v=d.db.get('conversation:'+self.cid);c.update(state='deleted',hold=True,purge_after=0,deleted_by='synthetic-admin',deleted_at=0);d.db.transact([('conversation:'+self.cid,c,v)])
        self.assertEqual(d.purge_due(),0);self.assertEqual(len(d.db.scan('chat-status:'+self.cid+':')),1)
        c,v=d.db.get('conversation:'+self.cid);c['hold']=False;d.db.transact([('conversation:'+self.cid,c,v)])
        self.assertEqual(d.purge_due(),1);self.assertEqual(d.db.scan('chat-status:'+self.cid+':'),[])
    def test_authentication_failure_does_not_use_source_recovery(self):
        self.switch_to_support()
        with self.model_error(401):r=self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':fixtures.SUPPORT})
        self.assertEqual(r.status_code,503);self.assertEqual(self.app.state.domain.db.scan('message:'+self.cid+':'),[])
    def test_source_change_after_provider_failure_blocks_answer(self):
        self.switch_to_support()
        def changed(*args):
            self.state['generation']='b'*32;raise GenerationFailure('provider_http_400')
        with patch('app.generation.invoke',changed):r=self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':fixtures.SUPPORT})
        self.assertEqual(r.status_code,503);self.assertEqual(self.app.state.domain.db.scan('message:'+self.cid+':'),[])
