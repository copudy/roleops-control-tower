"""Work boundaries enforced before retrieval/provider calls; no log-reading tool."""
import hashlib,json,re,unicodedata
from pathlib import Path

def archive_catalog():
    root=Path(__file__).parent.parent
    data=json.loads((root/'app/reference_lifecycle.json').read_bytes())
    for item in data['archived_documents']:
        path=root/item['path']
        if not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256'] or item['lifecycle_status']!='archived':
            raise ValueError('ARCHIVE_REFERENCE_INTEGRITY')
    return data['archived_documents']

def restricted_resource(question):
    q=unicodedata.normalize('NFKC',question).lower()
    # Requests for records differ from questions about approved manual methods.
    action=r'調閱|查閱|查看|查詢|列出|匯出|讀取|取得|顯示|下載|給我|幫我抓|提供|看一下|看|查|export|show|read'
    resource=r'(?:平台|系統|伺服器|cloudwatch|登入|登錄|其他員工|其他同事).{0,20}(?:日誌|紀錄|記錄|log\b)|(?:其他員工|其他同事|所有員工).{0,12}(?:登入|登錄)|cloudwatch.{0,8}logs?\b'
    if re.search(action,q) and re.search(resource,q):
        return {'resource':'platform_operations','permission':'ops:read'}
    return None

def requested_manual_version(question,history=()):
    q=unicodedata.normalize('NFKC',question).lower()
    if not re.search(r'手冊|維修|維運|m2',q):return None
    versions=re.findall(r'(?<!\d)(20\d{2}(?:\.\d+)?)(?:\s*(?:年|版))?',q)
    old=bool(re.search(r'舊版|舊手冊|過期|過時|封存|以前的手冊',q))
    if not old and not versions:return None
    catalog=archive_catalog();current='2026.1'
    if versions and all(x in ('2026','2026.1') for x in versions) and not old:return None
    requested=versions[0] if versions else catalog[0]['policy_version']
    archived=next((x for x in catalog if x['policy_version']==requested or x['policy_version'].split('.')[0]==requested),None)
    return {'requested_version':requested,'lifecycle':'archived' if archived else 'unapproved','current_version':current}

def restricted_answer(user,boundary):
    allowed=boundary['permission'] in user.get('permissions',[])
    if allowed:
        answer='你目前具有維運查閱權限。請從「系統維運 → 系統資訊與治理事件」查閱平台可提供的紀錄；CloudWatch 原始日誌由具 AWS 授權的人員在主控台查閱。助理對話不會直接讀取或匯出日誌。'
    else:
        answer='這項請求需要「維運查閱」權限，你目前的帳號沒有這項權限，因此無法提供平台日誌或其他員工的登入紀錄。請向具維運查閱權限的管理人員說明查核目的與範圍。你仍可詢問已授權維運手冊中的查核原則。'
    return {'answer':answer,'grounding':{'status':'denied' if not allowed else 'routed','facts':[],'suggestions':[], 'boundary':dict(boundary,allowed=allowed,checked_by='backend_permission')},'proposal':None}

def version_answer(policy,chunks):
    from .grounding import facts_for,decode_grounded,complete_source_evidence,require_coverage
    from .synthetic_policy import load_registry
    known=load_registry()['documents']['maintenance-manual']
    current=[x for x in chunks if x['document']['document_id']=='maintenance-manual' and x['document'].get('sha256')==known['sha256'] and x['document'].get('lifecycle_status')=='current' and x['text'] in known['body']]
    prefix=('你指定的手冊 '+policy['requested_version']+' 已封存，不能作為本次作業依據。' if policy['lifecycle']=='archived' else '目前沒有可核可使用的 '+policy['requested_version']+' 版手冊。')
    if not current:return {'answer':prefix+'目前也未取得你有權使用的現行手冊，請交具相應授權的人員確認。','grounding':{'status':'insufficient','facts':[],'suggestions':[],'version_policy':policy},'proposal':None}
    # Only version-bound, eligible current source sentences become guidance.
    facts=facts_for(chunks);ids=[k for k,v in facts.items() if v['document_id']=='maintenance-manual']
    result=decode_grounded(json.dumps({'status':'supported','facts':ids[:6],'suggestions':[]}),chunks)
    result['answer']=prefix+'目前可引用的有效版本為 '+known['policy_version']+'。\n\n'+ '\n'.join(f"{v['text']} [來源 {v['source_no']}，版本 {v['policy_version']}]" for v in result['grounding']['facts'])
    result['grounding']['version_policy']=policy;result['proposal']=None
    from .guidance_render import render_version
    return render_version(policy,result)


def needs_case_context(question,history=()):
    """A bare case-summary request needs an authorized case in this conversation."""
    from .case_evidence import intent
    chosen=intent(question,history)
    if chosen!=('support_case','approval'):return False
    q=unicodedata.normalize('NFKC',question).lower()
    if re.search(r'停機|退款|退費|客訴|cs-demo-01',q):return False
    for message in history:
        g=message.get('grounding') or {};coverage=g.get('coverage') or {}
        if (message.get('stamp') and message.get('generation') and g.get('status')=='supported'
            and coverage.get('subject') in ('support_case','maintenance_case')):return False
    return True
