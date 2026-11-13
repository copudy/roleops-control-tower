"""Readable presentation of already validated maintenance evidence.

Blank fields and contact suggestions are explicitly presentation advice.
They never assert observed device values or create a company procedure.
"""

def _references(facts):
    return '\n'.join('- '+f['text']+f" [來源 {f['source_no']}，版本 {f['policy_version']}]" for f in facts)

def _checklist():
    return ('【檢查範本：請填入本次資料】\n'
            '1. 設備型號：＿＿（依設備標示填寫）。\n'
            '2. 手冊版本與適用性：＿＿（核對現行版本是否適用這個型號）。\n'
            '3. 本次告警代碼：＿＿（依本次告警畫面記錄）。\n'
            '4. 現場狀態：＿＿（填目前觀察到的異常）。\n'
            '5. 執行人員資格：＿＿（資格與授權待確認／已確認）。\n'
            '以上是資料整理範本，空白欄位尚待你填寫；目前未確認任何現場值。')

def _contact():
    return ('【聯絡建議】\n'
            '若不知道誰具備本項授權，可以先聯絡所屬 IT／維運主管或當班負責人，請對方協助確認適任窗口與授權紀錄。'
            '這是聯絡建議；實際人員資格與作業分工仍依公司規範確認。')

def render_maintenance(question,history,result,report):
    if report.get('subject')!='maintenance_case' or not report.get('complete'):return result
    facts=result['grounding']['facts'];mode=report['case_mode']
    if mode=='verify_inputs':
        answer=('處理這次再次告警前，先確認手冊適用於設備，並把下列五項資料核對完整。\n\n'+_checklist()+
                '\n\n【下一步】\n適用性或人員資格尚未確認時，先保留執行結論；由具相應授權的維修人員依正式手冊判讀。\n\n'+_contact())
    elif mode=='delegate':
        answer=('【授權判讀人員】\n先確認「誰具備本項維修作業的資格與授權」，再交由該人員依現行手冊判讀。資格尚未確認前，先不要作成可執行的結論。\n\n'+_contact()+
                '\n\n【交接時可一併準備】\n設備型號、現行手冊版本、本次完整告警及現場狀態，方便授權人員核對適用性。')
        # The delegate plan also carries the five-field source proposition.
    elif mode=='alert':
        answer=('歷史有相似告警，可以作為查核線索；仍要核對這次設備與現場資料，才能由授權人員判讀。'
                '請先整理本次與歷史案件的差異，保留尚未確認的原因及執行結論。\n\n'+_checklist()+
                '\n\n【歷史線索與待補資訊與轉交】\n補入本次型號、版本、完整告警及現場差異，交具相應授權的維修人員查核。')
    elif mode in ('handover','summary_structure'):
        answer=('可以，以下是接手摘要範本。請填入實際觀察值，未知項目註明「待確認」。\n\n'
                '本次狀態：＿＿\n手冊適用性與人員資格：＿＿\n歷史線索與本次差異：＿＿\n待補資訊與轉交：＿＿\n'
                '這份摘要保留待現場查核項目，由具相應授權的人員判讀。')
        if mode=='summary_structure':
            answer=('交接摘要先分成四欄：本次已知狀態、手冊適用性、歷史線索、資訊缺口。'
                    '每欄記下已知內容與待確認項目，避免把歷史推測寫成這次已確認的事實。')
    elif mode=='history_requirements':
        answer=('請先補齊這次的設備型號、手冊版本、完整告警與現場差異，並核對執行人員資格。'
                '歷史 H1 的相似告警與當時原因，仍需依本次證據重新查核。\n\n'+_checklist())
    else:return result
    # Retain exact source sentences and their original per-fact citations.
    result=dict(result,answer=answer+'\n\n【文件依據】\n'+_references(facts))
    result['grounding']['presentation']={'kind':'maintenance_work_guide','template_values':'unconfirmed','contact_routing':'suggestion_only'}
    return result

def render_version(policy,result):
    facts=result['grounding']['facts']
    if result['grounding'].get('status')!='supported':return result
    prefix=(f"你指定的手冊 {policy['requested_version']} 已封存，不能作為本次作業依據。" if policy['lifecycle']=='archived'
            else f"目前沒有可核可使用的 {policy['requested_version']} 版手冊。")
    answer=(prefix+f"目前可引用的有效版本為 {policy['current_version']}。\n\n"
            '接下來先依現行手冊完成適用性及人員資格核對，再交授權維修人員判讀。\n\n'+_checklist()+
            '\n\n資格或手冊適用性尚未確認時，先保留執行結論；教材提供查核原則，沒有真實設備操作指令。\n\n'+_contact()+
            '\n\n【現行文件依據】\n'+_references(facts))
    return dict(result,answer=answer)

def verified_case_fallback(sc,error_code):
    """Source-only recovery for a complete, unique reviewed case plan."""
    from .grounding import coverage_report,decode_grounded,complete_source_evidence,require_coverage
    allowed={'provider_http_400','provider_http_429','provider_http_500','provider_http_502','provider_http_503','provider_http_504','provider_transport','provider_invalid_json','generation_response_contract'}
    if error_code not in allowed and not error_code.startswith('output_contract_'):return None
    report=coverage_report(sc['question'],sc['chunks'],[],sc['history'])
    if report.get('subject') not in ('support_case','maintenance_case') or report.get('method')!='version_bound_facets_v2':return None
    rows=report['parts']
    if not rows or any(len(row['supported_candidates'])!=1 for row in rows):return None
    if len({row['supported_candidates'][0] for row in rows})>6:return None
    result=decode_grounded('{"status":"insufficient","facts":[],"suggestions":[]}',sc['chunks'])
    result=complete_source_evidence(sc['question'],sc['chunks'],result,sc['history'])
    if result['grounding'].get('status')!='supported':return None
    result=require_coverage(sc['question'],sc['chunks'],result,sc['history'])
    result['grounding']['answer_origin']='verified_source_fallback'
    result['grounding']['provider_failure_code']=error_code
    result['grounding']['model_selected_fact_ids']=[]
    result['grounding']['model_status']='unavailable'
    result.update(proposal=None,usage=None)
    return result
