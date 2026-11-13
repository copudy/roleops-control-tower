"""Dependency-free release smoke for the backend coverage contract."""
import json, unittest
from pathlib import Path
from app.grounding import facts_for, coverage_report, decode_grounded, require_coverage, GroundingFailure,complete_source_evidence

class CoverageContract(unittest.TestCase):
    def setUp(self):
        reg=json.loads((Path(__file__).resolve().parents[1]/'app/synthetic_registry.json').read_text('utf-8'))
        d=reg['documents']['crm-access']
        self.chunks=[{'document':dict(d,document_id='crm-access'),'text':d['body']}]
        self.ids=list(facts_for(self.chunks))
    def test_supported_variants(self):
        for q in ('CRM申請條件？申請流程是什麼？','CRM資格與程序','CRM怎麼申請？'):
            self.assertTrue(coverage_report(q,self.chunks,self.ids)['complete'])
    def test_unsupported_mixed_short_questions(self):
        for q in ('CRM費用？','CRM條件及流程與收費','CRM核准要幾天？','申請流程？'):
            self.assertFalse(coverage_report(q,self.chunks,self.ids)['complete'])
    def test_workflow_requires_complete_evidence(self):
        for ids in (self.ids[:1],self.ids[1:2],[self.ids[0],self.ids[3]]):
            self.assertFalse(coverage_report('CRM申請流程',self.chunks,ids)['complete'])
    def test_unknown_fact_rejected(self):
        with self.assertRaises(GroundingFailure):
            decode_grounded(json.dumps(dict(status='supported',facts=['missing'],suggestions=[])),self.chunks)
    def test_changed_source_rejected(self):
        self.chunks[0]['document']['sha256']='0'*64
        self.assertFalse(coverage_report('CRM流程',self.chunks,self.ids)['complete'])
    def test_render_original_source(self):
        r=decode_grounded(json.dumps(dict(status='supported',facts=self.ids,suggestions=[])),self.chunks)
        r=require_coverage('CRM條件與流程',self.chunks,r)
        self.assertTrue(r['grounding']['coverage']['complete'])
    def test_source_completion_and_model_attribution(self):
        original=[self.ids[0],self.ids[3]]
        r=decode_grounded(json.dumps(dict(status='supported',facts=original,suggestions=[])),self.chunks)
        r=complete_source_evidence('CRM條件與流程',self.chunks,r)
        self.assertEqual(r['grounding']['model_selected_fact_ids'],original)
        self.assertIn(self.ids[1],r['grounding']['evidence_completion']['added_fact_ids'])
        self.assertTrue(require_coverage('CRM條件與流程',self.chunks,r)['grounding']['coverage']['complete'])
    def test_completion_never_hides_unknown_question(self):
        r=decode_grounded(json.dumps(dict(status='supported',facts=self.ids[:1],suggestions=[])),self.chunks)
        r=complete_source_evidence('CRM條件與流程與收費',self.chunks,r)
        self.assertNotIn('evidence_completion',r['grounding'])
        with self.assertRaises(GroundingFailure):require_coverage('CRM條件與流程與收費',self.chunks,r)

    def test_formal_compound_question_and_compositional_paraphrases(self):
        questions=(
            '我因為處理客戶案件，需要申請 CRM 系統存取權限。請根據目前有效的公司規定，說明申請時要提供哪些工作目的與使用期間資訊、由誰核准，以及核准後是否代表權限已經開通？我目前只想了解規則，不要替我建立申請。',
            '依據現行公司規則，請說明 CRM 申請時需提供哪些資料、由誰負責核准，以及核准後是否已經開通？',
            '請說明 CRM 工作目的、使用期間與核准人，以及核准後由誰開通。不要幫我提交申請。',
            '我為了辦理客服案件，需要申請 CRM 權限。請解釋申請理由、使用期限，以及由誰批准及如何開通？',
            'ＣＲＭ：工作目的及使用期間？由誰審批？核准後是否代表已開通？',
        )
        for q in questions:
            with self.subTest(q=q):
                report=coverage_report(q,self.chunks,self.ids)
                self.assertTrue(report['complete'],report)
                self.assertEqual(report['method'],'version_bound_facets_v2')
                self.assertEqual(request_intent_for_test(q),'guidance')

    def test_unknown_obligations_cannot_be_erased_by_grammatical_filler(self):
        for unknown in ('收費多少','核准需幾天','最多可以使用幾天','主管不在時誰可以例外核准','能不能自動延長期限','ERP 權限','個人資料可否匯出'):
            q='CRM 工作目的與使用期間，以及'+unknown+'？不要替我建立申請。'
            with self.subTest(unknown=unknown):
                r=coverage_report(q,self.chunks,self.ids)
                self.assertFalse(r['complete'])
                self.assertTrue(any(not x['supported_candidates'] for x in r['parts']))
                decoded=decode_grounded(json.dumps(dict(status='supported',facts=self.ids,suggestions=[])),self.chunks)
                self.assertNotIn('evidence_completion',complete_source_evidence(q,self.chunks,decoded)['grounding'])

    def test_followup_subject_comes_from_trusted_latest_turn_not_retrieval(self):
        previous={'question':'CRM申請條件與流程','stamp':'scope-test','generation':'generation-test',
                  'grounding':{'status':'supported','coverage':{'subject':'crm','complete':True}}}
        for q in ('那由誰核准，以及核准後誰開通？','上述申請要提供哪些資料？'):
            r=coverage_report(q,self.chunks,self.ids,[previous])
            self.assertTrue(r['complete'],r)
            self.assertEqual(r['subject'],'crm')
        r=coverage_report('那核准要幾天？',self.chunks,self.ids,[previous])
        self.assertFalse(r['complete'])
        for history in ([],[dict(previous,stamp=None)],[dict(previous,generation=None)],
                        [previous,{'question':'MCP Timeout','grounding':{'status':'supported'},'stamp':'s','generation':'g'}]):
            r=coverage_report('那由誰核准？',self.chunks,self.ids,history)
            self.assertNotEqual(r['method'],'version_bound_facets_v2')

    def test_provisioning_facet_cannot_be_completed_from_conditions_only(self):
        decoded=decode_grounded(json.dumps(dict(status='supported',facts=self.ids[:1],suggestions=[])),self.chunks)
        r=complete_source_evidence('CRM 工作目的及由誰開通？',self.chunks,decoded)
        self.assertNotIn('evidence_completion',r['grounding'])
        with self.assertRaises(GroundingFailure):require_coverage('CRM 工作目的及由誰開通？',self.chunks,r)

    def test_version_period_or_numerical_question_is_not_a_filler(self):
        for q in ('CRM 使用期間是 90 天嗎？','CRM 核准條件 2027 年是否相同？','CRM 工作目的可以省略嗎？','CRM 資料分享？','CRM 資訊分享？'):
            self.assertFalse(coverage_report(q,self.chunks,self.ids)['complete'])

def request_intent_for_test(question):
    from app.grounding import request_intent
    return request_intent(question,['crm'])[0]

if __name__=='__main__':unittest.main()
