"""Conversation status contract, separate from factual RAG messages and tools."""
from .domain import now,Denied
from .store import Conflict

def failure_view(code,trace):
    if code=='provider_http_429':
        kind='model_rate_limited';text='這次回覆未完成：模型服務已達使用量限制。請保留問題，稍後再試；尚未建立申請。'
    elif code.startswith('output_contract_') or code in ('provider_invalid_json','generation_response_contract'):
        kind='model_output_rejected';text='這次回覆未完成：產生的內容未通過文件或格式檢查。請保留問題，稍後再試；尚未建立申請。'
    elif code in ('source_unavailable','index_not_ready','source_or_generation_unavailable'):
        kind='source_unavailable';text='這次回覆未完成：目前無法核對可用文件。請稍後重試，或向管理員提供追蹤碼；尚未建立申請。'
    else:
        kind='model_service_unavailable';text='這次回覆未完成：模型服務未能完成這次請求。請保留問題，稍後重試，或向管理員提供追蹤碼；尚未建立申請。'
    return {'schema':1,'kind':'system_status','state':'failed','code':kind,'text':text,'trace_id':trace,
            'message_saved':False,'status_saved':False,'request_state':'not_created','retry':'manual'}

def saved_view(message):
    grounding=message.get('grounding') or {};proposal=message.get('proposal')
    kind='proposal' if proposal else 'access_denied' if grounding.get('status')=='denied' else 'clarification' if grounding.get('status')=='clarification' else 'answer'
    return {'schema':1,'kind':kind,'state':'saved','code':'reply_saved','text':'回覆已保存。',
            'trace_id':message['trace_id'],'message_id':message['id'],'message_saved':True,'status_saved':False,
            'request_state':'awaiting_confirmation' if proposal else 'not_created',
            'answer_origin':grounding.get('answer_origin','backend_boundary'),'retry':'none'}

def record_failure(domain,kb,user,cid,question,trace,code):
    """Keep a status activity, never a factual message or a proposal."""
    view=failure_view(code,trace)
    try:
        c,_=domain.conversation(user,cid)
        if c['owner_id']!=user['id'] or c['state']!='active' or c.get('hidden') or c.get('hold'):return view
        scope=domain.scope(domain.user(user['id']));generation=kb.status().get('generation')
        view=dict(view,status_saved=True,status_id=trace)
        activity={'id':trace,'conversation_id':cid,'kind':'system_status','role':'assistant','question':question,
                  'answer':view['text'],'conversation_status':view,'stamp':scope['stamp'],'generation':generation,
                  'at':now(),'trace_id':trace,'sources':[],'proposal':None,'exclude_from_generation':True}
        domain.db.transact([('chat-status:'+cid+':'+trace,activity,0)])
        return view
    except (Denied,Conflict):return dict(view,status_saved=False)

def request_view(result):
    """UI reference for known request results; uncertain tools require lookup."""
    status=result.get('status');dispatch=result.get('dispatch_status')
    if status in ('pending','approved','rejected'):
        text={'pending':'申請已建立，等待主管審核。','approved':'主管已核准這項申請；後續開通由權責單位辦理。','rejected':'主管已駁回這項申請。'}[status]
        return {'schema':1,'kind':'request_status','state':status,'text':text,'request_id':result.get('id'),'retry':'none'}
    if dispatch=='uncertain':
        return {'schema':1,'kind':'request_status','state':'uncertain','text':'送出結果待確認。請先查詢原申請，保留同一識別碼。','retry':'lookup_original'}
    return {'schema':1,'kind':'request_status','state':'unknown','text':'申請狀態待確認，請查詢原申請。','retry':'lookup_original'}
