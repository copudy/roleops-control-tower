"""Executable factual-support contract for the isolated synthetic workspace.

The model selects versioned facts from actual retrieval. It cannot supply a new
company assertion: the backend renders verified source propositions. Relevance
to the question is separately tested; this is not general semantic entailment.
"""
import hashlib,json,re

class GroundingFailure(Exception):
 def __init__(self,code):self.code=code;super().__init__(code)

def facts_for(chunks):
 facts={}
 for index,chunk in enumerate(chunks):
  doc=chunk['document']
  # Preserve complete sentences, including approval conditions and prohibitions.
  for sentence in re.findall(r'[^。！？\n]+[。！？]?',chunk['text']):
   sentence=sentence.strip()
   if not sentence:continue
   content_hash=hashlib.sha256(sentence.encode()).hexdigest()
   key=doc['document_id']+':'+doc['policy_version']+':'+content_hash[:16]
   facts[key]={'id':key,'text':sentence,'source_no':index+1,
    'document_id':doc['document_id'],'policy_version':doc['policy_version'],
    'source_sha256':doc['sha256'],'content_sha256':content_hash}
 return facts

def decode_grounded(content,chunks):
 try:obj=json.loads(content)
 except (TypeError,ValueError):raise GroundingFailure('invalid_answer_json') from None
 if not isinstance(obj,dict) or set(obj)!={'status','facts','suggestions'}:raise GroundingFailure('answer_schema')
 if obj['status'] not in ('supported','insufficient'):raise GroundingFailure('answer_status')
 if not isinstance(obj['facts'],list) or not isinstance(obj['suggestions'],list):raise GroundingFailure('answer_types')
 if len(obj['facts'])>6 or len(obj['suggestions'])>1:raise GroundingFailure('answer_limit')
 facts=facts_for(chunks);selected=[]
 for key in obj['facts']:
  if not isinstance(key,str) or key not in facts:raise GroundingFailure('unsupported_proposition')
  if key in [x['id'] for x in selected]:raise GroundingFailure('duplicate_proposition')
  selected.append(facts[key])
 if obj['status']=='insufficient':
  if selected or obj['suggestions']:raise GroundingFailure('insufficient_with_claims')
  return {'answer':'目前授權文件沒有足夠資料支持這個問題，請向相關負責窗口確認。','grounding':{'status':'insufficient','facts':[],'suggestions':[]}}
 if not selected:raise GroundingFailure('supported_without_evidence')
 suggestions=[];omitted=[]
 for suggestion in obj['suggestions']:
  if not isinstance(suggestion,str) or not 1<=len(suggestion)<=180:raise GroundingFailure('invalid_suggestion')
  # Suggestions cannot introduce policy, approval/provisioning, fields or states.
  if re.search(r'規定|必須|務必|應當|需要|須|核准|開通|承諾|退款|狀態|欄位|標示|勾選|簽署|簽名|轉交時間|更新為|改為|[「」]|\d',suggestion):
   omitted.append({'reason_code':'suggestion_contains_policy_or_state','sha256':hashlib.sha256(suggestion.encode()).hexdigest()});continue
  suggestions.append(suggestion)
 lines=['文件事實：']+[f"- {x['text']} [來源 {x['source_no']}，版本 {x['policy_version']}]" for x in selected]
 if suggestions:lines+=['建議（非公司規定）：']+['- '+s for s in suggestions]
 return {'answer':'\n'.join(lines),'grounding':{'status':'supported','facts':selected,'suggestions':suggestions,'omitted_suggestions':omitted}}

def request_intent(question,systems):
 q=question.lower().strip()
 if re.search(r'整理.{0,8}條件|有哪些條件|如何申請|誰.{0,4}核准|申請流程|不要.{0,8}建立|不.{0,4}申請',q):return 'guidance',None
 if not ('crm' in q and re.search(r'請協助建立|請幫我建立|幫我建立|建立.{0,12}待核准申請',q)):return 'guidance',None
 if 'crm' not in systems:raise GroundingFailure('unauthorized_request')
 match=re.search(r'(?:理由|原因|工作目的)\s*(?:是|為|[:：])\s*(.+?)[。.!！]?$',question)
 if not match:return 'clarify',None
 reason=match.group(1).strip().rstrip('。.!！')
 if not reason or len(reason)>1000:raise GroundingFailure('invalid_request_reason')
 return 'proposal',reason

def validate_native_proposal(result,question,systems):
 intent,reason=request_intent(question,systems)
 proposal=result.get('proposal')
 if intent!='proposal' or not proposal:raise GroundingFailure('missing_or_unrequested_native_proposal')
 if proposal!={'system_id':'crm','reason':reason}:raise GroundingFailure('proposal_not_user_authorized_parameters')
 return proposal


def coverage_report(question,chunks,selected_ids,history=()):
 from .coverage import report
 return report(question,facts_for(chunks),selected_ids,history)

def complete_source_evidence(question,chunks,result,history=()):
 """Complete only unambiguous, version-bound evidence obligations.

 No new assertion text is authored here. Unknown spans, absent evidence,
 unrelated model selections, ambiguous alternatives and >6 facts fail closed.
 Model-selected and backend-added IDs are separately attributed.
 """
 from .case_evidence import complete as complete_case
 case=complete_case(question,chunks,result,history,facts_for(chunks),decode_grounded)
 if case is not None:return case
 if result['grounding']['status']!='supported':return result
 original=[f['id'] for f in result['grounding']['facts']]
 report=coverage_report(question,chunks,original,history)
 if report.get('method')!='version_bound_facets_v2' or report['complete']:return result
 rows=report['parts'];available=facts_for(chunks);selected=set(original)
 if not rows or any(not r['supported_candidates'] for r in rows):return result
 candidates={key for r in rows for key in r['supported_candidates']}
 if not selected.issubset(candidates):return result
 facets={r['obligation'].split(':')[0] for r in rows}
 if any(not selected.intersection({key for r in rows if r['obligation'].startswith(facet+':') for key in r['supported_candidates']}) for facet in facets):return result
 final=set(selected)
 for row in rows:
  if final.intersection(row['supported_candidates']):continue
  if len(row['supported_candidates'])!=1:return result
  final.add(row['supported_candidates'][0])
 if len(final)>6:return result
 ordered=[key for key in available if key in final]
 completed=decode_grounded(json.dumps({'status':'supported','facts':ordered,'suggestions':result['grounding']['suggestions']}),chunks)
 completed['grounding']['omitted_suggestions']=result['grounding'].get('omitted_suggestions',[])
 completed['grounding']['model_selected_fact_ids']=original
 completed['grounding']['evidence_completion']={'method':'unambiguous_version_bound_source_closure_v1',
  'added_fact_ids':[key for key in ordered if key not in selected],
  'policy_fact_text_authored_by':'verified_source_only'}
 return completed


def require_coverage(question,chunks,result,history=()):
 if result['grounding']['status']!='supported':return result
 report=coverage_report(question,chunks,[f['id'] for f in result['grounding']['facts']],history)
 if not report['complete']:raise GroundingFailure('incomplete_subquestion_coverage')
 result['grounding']['coverage']=report
 from .case_evidence import render as render_case
 return render_case(question,history,result,report)
