import copy,json,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_case_api as fixtures
from app.case_evidence import intent
from app.grounding import request_intent,decode_grounded,complete_source_evidence,require_coverage
from app.it_maintenance_access import alias_access
from app.synthetic_policy import load_registry

QUESTIONS=[
 '依維修設備手冊，處理再次告警前，需要核對哪些資訊？',
 '如果手冊適用性或人員資格還沒確認，接下來應交給誰判讀？',
 '依維修歷史紀錄，接手摘要應分開記錄哪些項目？',
 '歷史案件 H1 有相似告警，本次還需要補哪些資訊才能查核？']
CRM='請幫我建立 CRM 待核准申請，理由是要調閱客戶資料以進行聯繫。'

class UnifiedPolicyTests(unittest.TestCase):
    def setUp(self):self.reg=load_registry()['documents']
    def doc(self,k):return dict(self.reg[k],document_id=k)
    def test_both_existing_actor_settings_gain_exact_approved_sources(self):
        for u in ({'role':'it','department':'IT','title':'IT 專員'},{'role':'maintenance','department':'IT','title':'維修工程師'},{'role':'maintenance','department':'維修部','title':'維修工程師'}):
            for d in ('crm-access','it-sop','maintenance-manual','maintenance-history'):
                with self.subTest(user=u['role'],doc=d):self.assertTrue(alias_access(self.doc(d),u))
    def test_no_finance_customer_or_wrong_department_expansion(self):
        u={'role':'it','department':'IT','title':'IT 專員'}
        for d in ('operations-report','support-case','support-contract'):self.assertFalse(alias_access(self.doc(d),u))
        self.assertFalse(alias_access(self.doc('maintenance-manual'),dict(u,department='HR')))
        self.assertFalse(alias_access(self.doc('maintenance-manual'),dict(u,role='support')))
    def test_source_version_and_metadata_cannot_be_forged(self):
        u={'role':'maintenance','department':'維修部','title':'維修工程師'}
        for field,value in [('sha256','0'*64),('policy_version','2099'),('allowed_roles',['maintenance']),('lifecycle_status','archived')]:
            d=self.doc('crm-access');d[field]=value;self.assertFalse(alias_access(d,u))
    def test_four_professional_questions_are_intents_not_literal_answers(self):
        modes=['verify_inputs','delegate','summary_structure','history_requirements']
        for q,mode in zip(QUESTIONS,modes):self.assertEqual(intent(q),('maintenance_case',mode))
        for q in ('請依手冊列出需要核對哪些資料','手冊適用性尚未確認，應交由誰判讀？'):
            # Unreviewed constructions remain conservative; no falsefacts.
            self.assertEqual(intent(q)[0],'maintenance_case')

class UnifiedFlowTests(unittest.TestCase):
    def setUp(self):
        fixtures.CaseApiTests.setUp(self)
        self.providers[-1].stop();self.providers.pop()
        class Response:
            status=200
            def __init__(self,raw):self.raw=raw
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def read(self,n):return self.raw
        def provider(request,**kw):
            self.provider_calls+=1;p=json.loads(request.data)
            if p.get('tools'):
                _,reason=request_intent(p['messages'][-1]['content'],['crm'])
                message={'content':'','tool_calls':[{'id':'synthetic-native-call','type':'function','function':{'name':'create_access_request','arguments':json.dumps({'system_id':'crm','reason':reason})}}]}
            else:message={'content':json.dumps({'status':'insufficient','facts':[],'suggestions':[]})}
            return Response(json.dumps({'choices':[{'finish_reason':'stop','message':message}],'usage':{'prompt_tokens':12,'completion_tokens':6}}).encode())
        p=patch('app.generation.urllib.request.urlopen',provider);p.start();self.providers.append(p)
        self.dispatches=0
        async def dispatch(u,system,reason,key):
            self.dispatches+=1
            return self.app.state.domain.create_request(u['id'],system,reason,key,'synthetic-mcp-trace')
        self.app.state.agent.call_tool=dispatch
    def tearDown(self):fixtures.CaseApiTests.tearDown(self)
    def send(self,q):return fixtures.CaseApiTests.send(self,q)
    def test_same_existing_maintenance_account_four_questions_and_CRM_approval_roundtrip(self):
        expected=['型號','授權判讀人員','本次已知狀態','不能推定本次相同']
        for q,term in zip(QUESTIONS,expected):self.assertIn(term,self.send(q)['answer'])
        proposal=self.send(CRM);self.assertEqual(proposal['proposal']['system_id'],'crm')
        body=dict(system_id='crm',reason=proposal['proposal']['reason'],confirmed=True,conversation_id=self.cid,message_id=proposal['id'],idempotency_key=proposal['id'])
        first=self.client.post('/api/requests',json=body);self.assertEqual(first.status_code,200,first.text);rid=first.json()['id'];self.assertEqual(first.json()['status'],'pending')
        self.assertEqual(self.client.post('/api/requests',json=body).json()['id'],rid);self.assertEqual(self.dispatches,1)
        self.assertEqual(self.client.post('/api/approvals/'+rid,json={'decision':'approved'}).status_code,403)
        manager=TestClient(self.app);login=manager.post('/api/auth/login',json={'login':'admin','password':'SyntheticPassword123!'});manager.headers['x-csrf-token']=login.json()['csrf']
        decision=manager.post('/api/approvals/'+rid,json={'decision':'approved'});self.assertEqual(decision.status_code,200,decision.text);manager.close()
        read=self.client.get('/api/uiux/requests/'+rid);self.assertEqual(read.status_code,200,read.text);self.assertEqual(read.json()['status'],'approved');self.assertEqual(read.json()['id'],rid)
    def test_original_it_actor_can_use_manual_and_native_CRM_proposal(self):
        d=self.app.state.domain;rows=[u for _,u,_ in d.db.scan('user:')];admin=next(u for u in rows if u['login']=='admin');staff=next(u for u in rows if u['login']=='staff')
        d.edit_user(admin,staff['id'],{'role':'it','department':'IT','title':'IT 專員'})
        login=self.client.post('/api/auth/login',json={'login':'staff','password':'SyntheticPassword123!'});self.client.headers['x-csrf-token']=login.json()['csrf']
        self.cid=self.client.post('/api/conversations',json={'title':'IT整合測試'}).json()['id']
        self.assertIn('告警代碼',self.send(QUESTIONS[0])['answer']);self.assertEqual(self.send(CRM)['proposal']['system_id'],'crm')
    def test_policy_revision_protects_old_and_rollback_history(self):
        result=self.send(QUESTIONS[0]);self.assertTrue(result['stamp'].endswith(':itops005'))
        self.assertNotEqual(result['stamp'],result['stamp'].removesuffix(':itops005'))
        rows=self.client.get('/api/conversations/'+self.cid+'/messages').json();self.assertEqual(rows[0]['answer'],result['answer'])
    def test_unsupported_maintenance_fact_does_not_become_invented_answer(self):
        result=self.send('告警再現，維修費用多少？');self.assertEqual(result['grounding']['status'],'insufficient')
