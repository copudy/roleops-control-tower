import copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from app.case_evidence import intent,retrieval_context
from app.grounding import facts_for,coverage_report,decode_grounded,complete_source_evidence,require_coverage
from app.synthetic_policy import load_registry
from app.knowledge import Knowledge,KnowledgeUnavailable
from app.config import Settings
from app.store import Store

class CaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        r=load_registry()['documents'];self.maintenance=[{'document':dict(r[d],document_id=d),'text':r[d]['body']} for d in ('maintenance-history','maintenance-manual')]
        self.support=[{'document':dict(r[d],document_id=d),'text':r[d]['body']} for d in ('support-case','support-contract','support-approval')]
    def finish(self,q,chunks,status='supported',history=()):
        ids=list(facts_for(chunks));result=decode_grounded(json.dumps({'status':status,'facts':ids[:1] if status=='supported' else [],'suggestions':[]}),chunks)
        return require_coverage(q,chunks,complete_source_evidence(q,chunks,result,history),history)
    def test_all_reported_alert_variants_complete(self):
        for q in ('告警再現，歷史相似，可以照做嗎？','現在系統告警再現，歷史相似，可以照做嗎？','警報再度發生，和前次案例很像，能直接沿用原處理方式嗎？'):
            with self.subTest(q=q):
                result=self.finish(q,self.maintenance,'insufficient');self.assertEqual(result['grounding']['status'],'supported');self.assertIn('不能推定本次相同',result['answer']);self.assertIn('告警代碼',result['answer']);self.assertIn('不提供執行結論',result['answer']);self.assertEqual(len(result['grounding']['facts']),5)
    def test_handover_sections_and_missing_values(self):
        history=[{'question':'告警再現，歷史相似，可以照做嗎？','stamp':'s','generation':'g','grounding':{'status':'supported','coverage':{'subject':'maintenance_case'}}}]
        result=self.finish('能否整理接手摘要給我',self.maintenance,history=history)
        for term in ('接手摘要','本次狀態','歷史線索','手冊適用性','待補資訊與轉交','待現場查核'):self.assertIn(term,result['answer'])
        self.assertNotIn('已確認設備',result['answer']);self.assertEqual(result['grounding']['model_selected_fact_ids'],list(facts_for(self.maintenance))[:1])
    def test_support_options_complete(self):
        result=self.finish('設備兩次停機，客戶要求退款，請整理方案',self.support,'insufficient')
        for term in ('客戶主張','案件證據缺口','合約核對','不能判定全額退款','未核准不得承諾退款'):self.assertIn(term,result['answer'])
        self.assertEqual(len(result['grounding']['facts']),6)
    def test_support_submission_materials_complete(self):
        result=self.finish('請整理呈核材料',self.support)
        self.assertIn('主管待決事項',result['answer']);self.assertIn('本平台不自動送簽或退款',result['answer']);self.assertIn('合約適用核對',result['answer'])
    def test_unknown_topics_and_hidden_clauses_not_completed(self):
        for q in ('告警再現，維修費用多少？','告警E012再現，可以重置嗎？','告警相似，請列出員工薪資','告警再現，可以照做嗎？還有核准退款','CRM申請流程','MCP timeout 怎麼排障','整理今天所有資料'):
            with self.subTest(q=q):self.assertIsNone(intent(q))
    def test_missing_manual_has_unmet_obligations(self):
        report=coverage_report('告警再現，歷史相似，可以照做嗎？',self.maintenance[:1],[])
        self.assertTrue(any(not row['supported_candidates'] for row in report['parts']))
    def test_changed_SHA_has_unmet_obligations(self):
        chunks=copy.deepcopy(self.maintenance);chunks[1]['document']['sha256']='0'*64
        self.assertTrue(any(not row['supported_candidates'] for row in coverage_report('整理接手摘要',chunks,[])['parts']))
    def test_changed_policy_version_has_unmet_obligations(self):
        chunks=copy.deepcopy(self.maintenance);chunks[1]['document']['policy_version']='2099'
        self.assertTrue(any(not row['supported_candidates'] for row in coverage_report('整理接手摘要',chunks,[])['parts']))
    def test_changed_fact_cannot_be_completed(self):
        chunks=copy.deepcopy(self.maintenance);chunks[1]['text']='合成竄改正文。'
        self.assertTrue(any(not row['supported_candidates'] for row in coverage_report('整理接手摘要',chunks,[])['parts']))
    def test_ambiguous_duplicate_source_not_completed(self):
        chunks=self.maintenance+copy.deepcopy(self.maintenance[1:]);chunks[-1]['document']['policy_version']='wrong'
        r=self.finish('整理接手摘要',chunks)
        self.assertTrue(all(x['policy_version']=='2026.1' for x in r['grounding']['facts']))
    def test_facts_are_exact_approved_source_sentences(self):
        result=self.finish('整理接手摘要',self.maintenance,'insufficient');known=facts_for(self.maintenance)
        for f in result['grounding']['facts']:self.assertEqual(f,known[f['id']])
    def test_CRM_contract_unchanged(self):
        self.assertIsNone(intent('CRM資格與程序'));self.assertIsNone(intent('請協助建立CRM待核准申請，理由：整理客戶資料'))
    def test_generic_intro_selection_is_replaced_by_complete_relevant_evidence(self):
        chunks=self.maintenance;key=next(k for k,f in facts_for(chunks).items() if f['text'].startswith('合成設備教材'))
        r=decode_grounded(json.dumps({'status':'supported','facts':[key],'suggestions':[]}),chunks)
        r=complete_source_evidence('告警再現，歷史相似，可以照做嗎？',chunks,r)
        r=require_coverage('告警再現，歷史相似，可以照做嗎？',chunks,r)
        self.assertIn(key,r['grounding']['evidence_completion']['omitted_model_fact_ids']);self.assertIn('人員資格',r['answer']);self.assertEqual(len(r['grounding']['facts']),5)

class RetrievalCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.getenv('ROLEOPS_TEST_TEMP'));self.root=Path(self.tmp.name)
        folder=self.root/'knowledge';folder.mkdir()
        for p in (Path(__file__).parent/'fixtures').glob('*.md'):(folder/p.name).write_bytes(p.read_bytes())
        cfg=Settings(mode='local',root=self.root/'runtime',knowledge=folder,public_origin='',mcp_secret='x'*32,embed_provider='default',embed_dimensions=1)
        self.kb=Knowledge(cfg,Store(cfg))
        self.kb.embedding_contract=lambda:{};self.kb.embedding=lambda *a,**kw:[1.0]
        self.generation='a'*32;self.state={'status':'ready','generation':self.generation,'digest':'synthetic','embedding_contract':{}}
        self.kb.status=lambda:self.state
        self.chunks=[{'document':{k:v for k,v in self.kb.parse(*source).items() if k!='body'},'text':self.kb.parse(*source)['body'],'vector':[1.0]} for source in self.kb.sources()]
        self.kb.indexroot.mkdir(exist_ok=True,parents=True);(self.kb.indexroot/'current.json').write_text(json.dumps({'generation':self.generation}))
        f=self.kb.indexroot/self.generation;f.mkdir();raw=json.dumps(self.chunks,ensure_ascii=False).encode();(f/'chunks.json').write_bytes(raw);(f/'manifest.json').write_text(json.dumps({'generation':self.generation,'digest':'synthetic','checksums':{'chunks.json':hashlib.sha256(raw).hexdigest()}}))
        class RankOne:
            def __init__(self,*a):pass
            def add(self,v):self.count=len(v)
            def search(self,v,k):return np.array([[0.9]+[0.0]*(k-1)]),np.array([list(range(k))])
        import app.knowledge as module
        self.patch=patch.object(module.faiss,'IndexFlatIP',RankOne,create=True);self.patch.start()
        self.user={'role':'maintenance','department':'維修部','title':'維修工程師'};self.scope={'knowledge':['policy','maintenance']}
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_real_retrieve_adds_authorized_manual_below_vector_threshold(self):
        rows,g=self.kb.retrieve('告警再現，歷史相似，可以照做嗎？',self.user,self.scope)
        self.assertEqual({x['document']['document_id'] for x in rows},{'maintenance-history','maintenance-manual'});self.assertEqual(g,self.generation)
    def test_real_retrieve_handover_reads_raw_CRLF_successfully(self):
        rows,_=self.kb.retrieve('整理接手摘要',self.user,self.scope);self.assertEqual(len(rows),2)
    def test_wrong_department_cannot_gain_documents(self):
        rows,_=self.kb.retrieve('整理接手摘要',dict(self.user,department='HR'),self.scope);self.assertEqual(rows,[])
    def test_wrong_title_cannot_gain_documents(self):
        rows,_=self.kb.retrieve('整理接手摘要',dict(self.user,title='IT專員'),self.scope);self.assertEqual(rows,[])
    def test_role_cannot_gain_documents_from_another_family(self):
        u={'role':'support','department':'客服部','title':'客服專員'}
        rows,_=self.kb.retrieve('整理接手摘要',u,{'knowledge':['support','handover','policy']});self.assertEqual(rows,[])
    def test_source_tampering_after_index_still_rejected(self):
        p=self.kb.cfg.knowledge/'維修設備手冊.md';p.write_bytes(p.read_bytes()+b'changed')
        with self.assertRaises(KnowledgeUnavailable):self.kb.retrieve('整理接手摘要',self.user,self.scope)
    def test_index_manifest_tampering_still_rejected(self):
        (self.kb.indexroot/self.generation/'chunks.json').write_bytes(b'[]')
        with self.assertRaises(KnowledgeUnavailable):self.kb.retrieve('整理接手摘要',self.user,self.scope)
