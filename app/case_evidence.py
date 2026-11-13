"""Reviewed case evidence plans. IDs/hashes bind every rendered source claim.

Plans cover work intents, not literal demonstration questions. ACL-filtered,
current index chunks remain the only source; no external data or inferred
equipment/customer state is introduced. CRM/MCP retain their existing paths.
"""
import hashlib,json,re,unicodedata
from pathlib import Path

def registry():
    return json.loads(Path(__file__).with_name('case_registry.json').read_bytes())

# Compositional reviewed vocabulary. Unknown residual content is not discarded.
# In particular, arbitrary model names, alarm codes, fees, hidden extra clauses
# and unreviewed procedures cannot gain a complete-case answer.
GRAMMAR=tuple('''目前 現在 這次 本次 我們 我 本人 的 同樣 相同 類似 相似 很像 再現 再發 再次 再度 又 發生 出現
告警 警報 系統 設備 維修 維運 歷史 前次 前例 以前 之前 案例 紀錄 記錄 經驗 原 本來 做法 方式 處理
可不可以 可以 不能 不可以 不可 是否 能否 能 會不會 應該 要不要 要 不要 不 用 須 需 需要 直接
照做 照搬 沿用 套用 判讀 判斷 核對 確認 查核 依據 資格 適用 手冊 版本 型號 人員 現場 狀態 差異
請 請問 請說明 請整理 請協助 幫我 給我 告訴我 整理 補齊 列出 說明 摘要 接手 交接 清單 重點
資訊 缺口 缺少 哪些 有哪些 什麼 甚麼 如何 怎麼 做 才 行 嗎 呢 和 與 及 或 以及 並且 還有 同時
客服 客訴 客戶 客人 陳述 主張 要求 設備 兩次 停機 影響 營運 退款 全額 退費 方案 呈核 送簽 材料
合約 保固 條件 證據 附件 主管 待決 事項 轉交 授權 窗口 交給 後續 責任 判定 支持 根據 依照 按照
那 上述 剛才 這個 這份 這項 上一題 前面 有 沒有 是否 幫 忙 it 依 如果 若 適用性 相應 正式 還沒 尚未 未 尚 還 接下來 應 誰 分開 項目 案件 補 才能 前 怎麼辦 你 一下 依此 資料 交由 同仁 員工 客服人員 判讀前 開始 為何 為什麼 以前遇過 直接沿用 政策 契約 時間線 客戶主張 事實 待決事項 呈核摘要 補充 送給 送交 給主管 主管看 能做怎樣 先做 可以先 協助 以便 用來 三個 三項 大綱 草稿 決策 重複 判斷前 檢查 注意 要點 允許 條款 缺乏 詳細 確認一下 原因 對方 索賠 申訴 支持 請列一下 尚缺 建議 重整 成 看 可供 說 選項 可 現行 先 條件 由 把 讓 下一位 檢查 交 送'''.split())

def reviewed_question(q):
    remaining=re.sub(r'cs-demo-01|(?<![a-z0-9])(?:h1|m2)(?![a-z0-9])',' ',q)
    for word in sorted(set(GRAMMAR),key=len,reverse=True):remaining=remaining.replace(word,' ')
    return not re.sub(r'[\s？?；;。，,！!：:（）()「」“”\"\'、—-]+','',remaining)

def intent(question,history=()):
    q=unicodedata.normalize('NFKC',question).lower().strip()
    if re.search(r'\bcrm\b|\bmcp\b|資訊支援|api|密碼|開通|存取申請',q):return None
    # Specific facts/actions outside these synthetic sources stay with the
    # original unsupported-question guard rather than receive case boilerplate.
    if re.search(r'多少|幾天|幾小時|幾分鐘|金額|費用|收費|溫度|電壓|故障原因|如何修|怎麼修|如何重啟|怎麼重啟|重置步驟|刪除|核准退款',q):return None
    if not reviewed_question(q):return None
    manual=bool(re.search(r'維修|維運|手冊|人員資格',q))
    history_ref=bool(re.search(r'歷史|案件\s*h1',q))
    if manual and re.search(r'誰|交給|交由|轉交',q) and re.search(r'判讀|確認|查核',q):return ('maintenance_case','delegate')
    if manual and re.search(r'核對|確認|查核|檢查',q) and re.search(r'哪些|什麼|資訊|資料',q):return ('maintenance_case','verify_inputs')
    if re.search(r'接手|交接',q) and re.search(r'哪些項目|分開記錄|哪些資訊',q):return ('maintenance_case','summary_structure')
    if history_ref and re.search(r'補|缺口|缺少',q) and re.search(r'資訊|資料|查核',q):return ('maintenance_case','history_requirements')
    maintenance=bool(re.search(r'告警|警報|維修|維運',q)) and bool(re.search(r'再現|再發|再次|相似|類似|照做|照搬|沿用|套用|判讀|核對|依據|資格',q))
    support=bool(re.search(r'客服|客訴|停機|退款|退費|合約',q)) and bool(re.search(r'方案|呈核|退款|退費|材料|處理',q))
    if maintenance and support:return None
    if maintenance:return ('maintenance_case','alert')
    if support:return ('support_case','approval' if re.search(r'呈核|摘要|材料|送簽',q) else 'options')
    if re.search(r'接手|交接',q) and re.search(r'摘要|整理|清單|重點',q):
        if '客服' in q:return ('support_case','approval')
        if '接手' in q or re.search(r'維運|維修|告警|警報',q):return ('maintenance_case','handover')
    if re.search(r'呈核|送簽',q) and re.search(r'整理|摘要|材料|清單',q):return ('support_case','approval')
    if re.search(r'^(?:請|能否|可以|幫我|再|那|上述|剛才|這個|這份|\s)*(?:整理|補齊|列出).*(?:摘要|材料|清單)',q) and history:
        previous=history[-1];g=previous.get('grounding') or {};coverage=g.get('coverage') or {}
        if previous.get('stamp') and previous.get('generation') and g.get('status')=='supported':
            subject=coverage.get('subject')
            if subject=='maintenance_case':return subject,'handover'
            if subject=='support_case':return subject,'approval'
    return None

def retrieval_context(allowed,retrieved,question):
    # The caller has already applied Knowledge.eligible to every allowed row.
    # Current request is the final line of the backend's bounded history query.
    lines=[x for x in question.splitlines() if x.strip()]
    request=lines[-1] if lines else question
    selected=intent(request)
    if selected is None and len(lines)>1:
        previous=intent(lines[-2])
        if previous and reviewed_question(request.lower()) and re.search(r'摘要|接手|交接',request):selected=(previous[0],'handover' if previous[0]=='maintenance_case' else 'approval')
    if re.search(r'(?i)\bcrm\b',request):
        from .grounding import request_intent
        if request_intent(request,['crm'])[0] in ('proposal','clarify'):
            known=json.loads(Path(__file__).with_name('synthetic_registry.json').read_bytes())['documents']['crm-access']
            return [dict(row,score=1.0) for row in allowed if row['document']['document_id']=='crm-access' and row['document']['sha256']==known['sha256'] and row['document']['policy_version']==known['policy_version']]
    if selected is None:return retrieved
    r=registry();family=r['families'][selected[0]];expected=family['documents']
    candidates=[]
    for row in allowed:
        doc=row['document'];known=expected.get(doc['document_id'])
        if known and doc.get('sha256')==known['sha256'] and doc.get('policy_version')==known['policy_version']:
            candidates.append(dict(row,score=next((x.get('score',0) for x in retrieved if x['document']==doc),0),evidence_completion='reviewed_case_family_v1'))
    # Return only this authorized case family; missing documents become explicit
    # unmet obligations, never an excuse to use another employee's sources.
    return candidates

def plan(question,facts,selected_ids,history=()):
    recognized=intent(question,history)
    if recognized is None:
        q=unicodedata.normalize('NFKC',question).lower()
        if re.search(r'\bcrm\b|\bmcp\b|資訊支援|存取申請',q):return None
        subject='maintenance_case' if re.search(r'告警|警報|維修|維運|接手摘要',q) else 'support_case' if re.search(r'客服|客訴|退款|呈核',q) else None
        if subject is None:return None
        return {'applicable':True,'complete':False,'method':'version_bound_facets_v2','subject':subject,
                'case_mode':'unsupported','parts':[{'obligation':'unrecognized_question_span',
                'span_sha256':hashlib.sha256(q.encode()).hexdigest(),'supported_candidates':[],'covered':False}]}
    subject,mode=recognized;r=registry();family=r['families'][subject]
    rows=[]
    for index,item in enumerate(family['plans'][mode]):
        annotation=family['documents'][item['document_id']]
        candidates=[key for key,value in facts.items() if value['document_id']==item['document_id']
            and value['source_sha256']==annotation['sha256'] and value['policy_version']==annotation['policy_version']
            and value['content_sha256']==item['content_sha256']]
        rows.append({'obligation':subject+':'+mode+':'+str(index+1),'heading':item['heading'],'supported_candidates':candidates,
                     'covered':bool(set(selected_ids).intersection(candidates))})
    return {'applicable':True,'complete':bool(rows) and all(x['covered'] for x in rows),
            'method':'version_bound_facets_v2','subject':subject,'case_mode':mode,'parts':rows}

def complete(question,chunks,result,history,facts,decode):
    original=[f['id'] for f in result['grounding']['facts']]
    report=plan(question,facts,original,history)
    if report is None:return None
    rows=report['parts']
    # Complete only the unique reviewed evidence set. A changed/missing source,
    # ambiguous fact or unknown question remains unsupported.
    if not rows or any(len(row['supported_candidates'])!=1 for row in rows):return result
    required=[row['supported_candidates'][0] for row in rows]
    family=registry()['families'][report['subject']]['documents']
    eligible_ids={key for key,value in facts.items() if value['document_id'] in family
                  and value['source_sha256']==family[value['document_id']]['sha256']
                  and value['policy_version']==family[value['document_id']]['policy_version']}
    if len(set(required))>6 or not set(original).issubset(eligible_ids):return result
    ordered=list(dict.fromkeys(required))
    completed=decode(json.dumps({'status':'supported','facts':ordered,'suggestions':[]}),chunks)
    completed['grounding'].update(model_selected_fact_ids=original,model_status=result['grounding']['status'],
        evidence_completion={'method':'unique_version_bound_case_evidence_v1','added_fact_ids':[x for x in ordered if x not in original],
                             'omitted_model_fact_ids':[x for x in original if x not in ordered],
                             'policy_fact_text_authored_by':'verified_source_only'})
    return completed

def render(question,history,result,report):
    if report.get('subject') not in ('maintenance_case','support_case'):return result
    if report.get('subject')=='maintenance_case':
        from .guidance_render import render_maintenance
        return render_maintenance(question,history,result,report)
    by_id={f['id']:f for f in result['grounding']['facts']}
    title={'alert':'告警判讀依據與待確認事項','handover':'接手摘要','options':'案件方案與證據缺口','approval':'呈核材料摘要','verify_inputs':'手冊核對資訊','delegate':'適用限制與授權判讀','summary_structure':'接手摘要應記錄項目','history_requirements':'歷史案例差異與待補資訊'}[report['case_mode']]
    introductions={
        'alert':'先核對本次條件，再由具相應授權的人員判讀。以下分開整理歷史線索與現行手冊要求。',
        'handover':'可以。以下整理成交接草稿，現場尚未確認的資料請補入。',
        'options':'可以先整理給主管判斷的材料。以下區分客戶主張、待查證據與可呈核的處理方向。',
        'approval':'以下是給主管的呈核草稿。請先核對案件與附件，再透過公司既有簽核管道送出。',
        'verify_inputs':'再次告警時，先把設備與人員條件核對完整。手冊要求如下。',
        'delegate':'請交由具相應授權的維修人員，依正式手冊判讀；目前先保留執行結論。',
        'summary_structure':'交接時，請把本次狀態、手冊適用性、歷史線索與資訊缺口分開整理。',
        'history_requirements':'需要補齊本次設備與現場資料，才能查核歷史案件是否適用。'}
    lines=[introductions[report['case_mode']]]
    if report['case_mode'] in ('handover','approval'):
        previous=next((m.get('question','') for m in reversed(history) if intent(m.get('question','')) and intent(m.get('question',''))[0]==report['subject']),None)
        if previous:lines+=['本次使用者提問（待現場查核）：'+previous[:500]]
        lines+=['實際現場值與核對結果，請依下列教材要求補入；本文不判定已確認。']
    for row in report['parts']:
        chosen=next((by_id[x] for x in row['supported_candidates'] if x in by_id),None)
        if chosen:lines += [row['heading']+'：',f"- {chosen['text']} [來源 {chosen['source_no']}，版本 {chosen['policy_version']}]" ]
    result=dict(result,answer='\n'.join(lines))
    return result
