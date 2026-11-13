import json,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.web import create_app
from app.config import PERMISSIONS
import test_cases as fixtures

class CaseApiTests(unittest.TestCase):
    # Existing retrieval/ACL tests also run against the same app fixture.
    def setUp(self):
        fixtures.RetrievalCaseTests.setUp(self);self.kb.cfg.metrics_enabled=False;self.kb.cfg.generation_provider='groq';self.kb.cfg.embed_dimensions=1024
        self.app=create_app(self.kb.cfg);kb=self.app.state.kb;kb.status=lambda:self.state;kb.embedding_contract=lambda:{};kb.embedding=lambda *a,**kw:[1.0]
        d=self.app.state.domain
        admin=d.create_user(None,'admin','合成管理者','平台管理','管理員','supervisor','SyntheticPassword123!',list(PERMISSIONS),bootstrap=True)
        u=d.create_user(d.user(admin['id']),'staff','合成維運同仁','維修部','維修工程師','maintenance','SyntheticPassword123!',[])
        a=d.save_agent(d.user(admin['id']),None,{'name':'IT工作助理','avatar':'🤖','profile':'maintenance','prompt_version':'B'});d.assign(d.user(admin['id']),u['id'],a['id'])
        self.client=TestClient(self.app);login=self.client.post('/api/auth/login',json={'login':'staff','password':'SyntheticPassword123!'});self.client.headers['x-csrf-token']=login.json()['csrf']
        self.cid=self.client.post('/api/conversations',json={'title':'本次雙案例驗證'}).json()['id'];self.mode='insufficient';self.provider_calls=0
        class Response:
            status=200
            def __init__(self,raw):self.raw=raw
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def read(self,n):return self.raw
        def provider(request,**kwargs):
            self.provider_calls+=1;p=json.loads(request.data);ids=p['response_format']['json_schema']['schema']['properties']['facts']['items']['enum']
            answer={'status':self.mode,'facts':ids[:1] if self.mode=='supported' else [],'suggestions':[]}
            return Response(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],'usage':{'prompt_tokens':12,'completion_tokens':6}}).encode())
        self.providers=[patch('app.generation.read_secret',return_value='synthetic-not-a-real-key'),patch('app.generation.urllib.request.urlopen',provider)]
        for p in self.providers:p.start()
    def tearDown(self):
        for p in reversed(self.providers):p.stop()
        self.client.close();fixtures.RetrievalCaseTests.tearDown(self)
    def send(self,q):
        r=self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':q});self.assertEqual(r.status_code,200,r.text);return r.json()
    def test_full_pipeline_false_insufficient_completed_from_unique_source_and_saved(self):
        result=self.send('告警再現，歷史相似，可以照做嗎？');self.assertIn('不能推定本次相同',result['answer']);self.assertIn('人員資格',result['answer']);self.assertEqual(result['grounding']['model_status'],'insufficient');self.assertEqual(len(result['sources']),2)
        rows=self.client.get('/api/conversations/'+self.cid+'/messages').json();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['answer'],result['answer']);self.assertEqual(self.provider_calls,1)
    def test_partial_model_summary_completed_and_previous_user_context_labeled(self):
        self.send('告警再現，歷史相似，可以照做嗎？');self.mode='supported';result=self.send('整理接手摘要')
        for term in ('本次狀態','手冊適用性','歷史線索','待補資訊與轉交','待現場查核'):self.assertIn(term,result['answer'])
        rows=self.client.get('/api/conversations/'+self.cid+'/messages').json();self.assertEqual(len(rows),2);self.assertEqual(self.provider_calls,2)
    def test_unknown_numeric_question_remains_insufficient_without_provider(self):
        result=self.send('告警再現，維修費用多少？');self.assertEqual(result['grounding']['status'],'insufficient');self.assertEqual(self.provider_calls,0)
