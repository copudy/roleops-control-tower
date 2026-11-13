"""Backend-owned, version-bound coverage for a bounded access-policy vocabulary.

Annotations contain hashes, never answer prose. A facet may require several
propositions (AND of OR groups). Unknown residual text is an uncovered obligation,
not an invitation to let the model judge its own evidence. This is deliberately
not a general Chinese entailment engine. Other subjects retain the lexical guard.
"""
import hashlib, json, re, unicodedata
from pathlib import Path

FACETS = {
 'approval_duration': r'(?:主管)?(?:核准|批准|審批)(?:通常|一般|大約|大概)?(?:需要|需|要)?(?:幾個工作天|多少時間|多少天|幾小時|幾天|多久)',
 'workflow': r'申辦方式|辦理方式|申請方式|如何申請|怎麼申請|怎樣申請|如何辦理|怎麼辦理|流程|程序|步驟',
 'conditions': r'說明哪些資訊|提供哪些資料|提供哪些資訊|準備哪些資料|準備什麼|提供什麼|填寫什麼|說明什麼|(?:工作目的|使用期間|使用期限|申請理由)(?:的)?(?:資訊|資料)?|條件|資格|前提|要件',
 'approval': r'由誰(?:負責)?(?:核准|批准|審批)|誰(?:負責)?(?:核准|批准|審批)|哪個主管核准|哪位主管(?:核准|審批)|核准人|審批人',
 'provisioning': r'核准是否等於已經開通|核准是否等於開通|誰開通|如何開通|怎麼開通|開通',
 'credentials': r'帳密|密碼|分享',
 'limits': r'助理能做什麼|助理可以做什麼|直接授予|直接授權',
}
# Only grammatical filler in this bounded question language. Do not strip
# arbitrary nouns, quantities, negations or additional domain terms.
FILLER = ('公司員工','公司','員工','使用者','如果','假如','需要','應該','符合',
 '申請','系統','存取','權限','請問','請說明','請列出','請整理','請告訴我',
 '有哪些','哪些','是什麼','什麼','是甚麼','甚麼','是如何','如何','怎麼',
 '以及','並且','還有','同時','與','和','及','或','、','可以','是否','能否',
 '可不可以','可否','不可','不能','不可以','要','需','的','是','嗎','呢','我','該','應','才','直接')

# Reviewed discourse is matched as complete constructions, never an arbitrary
# .+ background span. These clauses neither request a fact nor authorize a tool.
DISCOURSE = (
 r'(?:請)?(?:依|根據|依據|依照|按照)(?:目前|現行|現在)(?:公司)?(?:存取)?(?:規則|規定|政策)',
 r'(?:我|本人)(?:因為|為了)(?:處理|辦理)(?:客戶案件|客服案件|客戶業務)',
 r'(?:請)?(?:根據|依據|依照|按照)(?:目前|現行|現在)?(?:有效的?)?(?:公司)?(?:規定|規則|政策)',
 r'(?:我)?(?:目前|現在)?(?:只想|只是想|只要|僅想|只)(?:了解|瞭解|知道|詢問)(?:相關)?(?:規則|規定|流程)',
 r'(?:請)?(?:不要|不用|不必|無需)(?:替我|幫我|代我)?(?:建立|提出|提交|送出)(?:任何)?(?:申請|提案)',
)
# Function words around *identified* facets. Domain nouns, numeric bounds,
# exceptions and unsupported topics remain residual obligations and fail closed.
QUESTION_GRAMMAR = (
 '核准之後','核准以後','核准後','依核准內容','目前有效的','需要申請',
 '申請時','申請的時候','請說明','請解釋','請介紹','說明',
 '由誰','由','負責','時要','時需','提供','填寫','準備','列出','明確',
 '預計','預期','已經','已','就','代表','等於','表示','以及','還是',
 '有哪些','哪些','什麼','甚麼','關於','這項','這個','上述','剛才','那麼','那',
)

def digest(text): return hashlib.sha256(text.encode()).hexdigest()
def normalize(text): return unicodedata.normalize('NFKC',text).lower()

def access_plan(question, facts, history=()):
    q=normalize(question)
    # Resolve a bounded anaphoric follow-up only from the immediately preceding
    # backend-supplied, scope/generation-filtered, supported turn. Never infer
    # identity or subject from retrieval rank; never reuse its facts as evidence.
    explicit=bool(re.search(r'(?<![a-z0-9])crm(?![a-z0-9])',q))
    if not explicit:
        previous=history[-1] if history else {}
        prior=previous.get('grounding') or {}
        coverage=prior.get('coverage') or {}
        anchored=bool(re.match(r'\s*(?:那|上述|剛才|這項|這個)',q))
        trusted=previous.get('stamp') and previous.get('generation') and prior.get('status')=='supported'
        subject=bool(re.search(r'(?<![a-z0-9])crm(?![a-z0-9])',normalize(previous.get('question','')))) or coverage.get('subject')=='crm'
        if not (anchored and trusted and subject):return None
    remaining=re.sub(r'(?<![a-z0-9])crm(?![a-z0-9])',' ',q)
    remaining=re.sub(r'\s+','',remaining)
    for pattern in DISCOURSE:remaining=re.sub(pattern,' ',remaining)
    requested=[]
    for name,pattern in FACETS.items():
        if re.search(pattern,remaining):
            requested.append(name);remaining=re.sub(pattern,' ',remaining)
    for word in sorted(set(FILLER+QUESTION_GRAMMAR),key=len,reverse=True): remaining=remaining.replace(word,' ')
    residual=re.sub(r'[\s？?；;。，,！!：:（）()「」"\n]+','',remaining)
    registry=json.loads((Path(__file__).with_name('coverage_registry.json')).read_text('utf-8'))
    annotation=registry['documents'].get('crm-access',{})
    valid={k:f for k,f in facts.items() if f['document_id']=='crm-access'
      and f['policy_version']==annotation.get('policy_version')
      and f['source_sha256']==annotation.get('source_sha256')}
    rows=[]
    for facet in requested:
        groups=annotation.get('facets',{}).get(facet,[])
        for i,alternatives in enumerate(groups):
            candidates=[k for k,f in valid.items() if f['content_sha256'] in alternatives]
            rows.append({'obligation':facet+':'+str(i+1),'supported_candidates':candidates})
        if not groups: rows.append({'obligation':facet,'supported_candidates':[]})
    if residual or not requested:
        rows.append({'obligation':'unrecognized_question_span','span_sha256':digest(residual or q),'supported_candidates':[]})
    return rows

def report(question, facts, selected_ids, history=()):
    selected=set(selected_ids)
    if not selected.issubset(facts):
        return {'applicable':True,'complete':False,'method':'invalid_fact_id','parts':[]}
    from .case_evidence import plan as case_plan
    case=case_plan(question,facts,selected_ids,history)
    if case is not None:return case
    rows=access_plan(question,facts,history)
    if rows is not None:
        for row in rows: row['covered']=bool(selected.intersection(row['supported_candidates']))
        return {'applicable':True,'complete':bool(rows) and all(r['covered'] for r in rows),
          'method':'version_bound_facets_v2','subject':'crm','parts':rows}
    # Preserve the threshold for subjects outside the reviewed facet language.
    # Check short single questions too; absence of punctuation is not evidence.
    parts=[p.strip() for p in re.split(r'[？?；;。\n]+',question) if p.strip()]
    def grams(text):
        words=re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+',normalize(text))
        return {g for w in words for g in ([w] if w.isascii() else [w[i:i+2] for i in range(len(w)-1)])}
    rows=[]
    for part in parts:
        q=grams(part);scores={k:len(q & grams(f['text'])) for k,f in facts.items()}
        best=max(scores.values(),default=0)
        matches=[k for k,v in scores.items() if v==best and v>=2]
        rows.append({'subquestion_sha256':digest(part),'best_overlap':best,
          'supported_candidates':matches,'covered':bool(selected.intersection(matches))})
    return {'applicable':True,'complete':bool(rows) and all(r['covered'] for r in rows),
      'method':'legacy_lexical_threshold_2','parts':rows}
