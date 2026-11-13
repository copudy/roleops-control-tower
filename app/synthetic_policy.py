"""Backend synthetic-demo export policy; no LLM classification or fixed questions.

Known patterns are defense in depth, NOT proof of arbitrary text being harmless.
The only supported deployment is the isolated, engineer-owned synthetic workspace.
"""
import hashlib,json,re,unicodedata
from pathlib import Path

SAFE_REPLY='此內容不符合合成展示環境的安全規則，已停止處理；未呼叫外部模型或提交工具。請改以不含敏感資料、且符合目前角色權限的工作問題提問。'
class PolicyDenied(Exception):
 def __init__(self,code):self.code=code;super().__init__(code)
def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def normalized(text):
 return unicodedata.normalize('NFKC',re.sub('[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]','',text)).lower()
def inspect_text(text,phase='question',role='support'):
 if not isinstance(text,str) or len(text)>18000:raise PolicyDenied('invalid_text')
 t=normalized(text)
 if any(ord(c)<32 and c not in '\n\r\t' for c in text):raise PolicyDenied('control_encoding')
 if re.search(r'(?:gsk_|sk-proj-|akia)[a-z0-9_-]{12,}|-----begin .*private key|(?:api[_ -]?key|密碼|password|token)\s*[:=：]\s*\S{5,}',t):raise PolicyDenied('credential')
 if re.search(r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|(?:\+?886[- ]?|0)9\d{2}[- ]?\d{3}[- ]?\d{3}',t):raise PolicyDenied('personal_data')
 if re.search(r'(?:真實(?:企業|公司|客戶)|公司機密|企業機密|confidential|secret[_ -]?marker|機密夾帶)',t):raise PolicyDenied('unapproved_data')
 if re.search(r'(?:https?://|[a-z0-9+/]{64,}={0,2}|(?:\\u[0-9a-f]{4}){3,}|(?:%[0-9a-f]{2}){6,})',t):raise PolicyDenied('unreviewable_external_or_encoding')
 if re.search(r'(?:忽略|覆蓋|跳過|無視|ignore|override|disregard).{0,28}(?:規則|指令|提示|授權|安全|previous|instruction|system|policy)|(?:system|developer)\s*:|<\|(?:system|im_start)|(?:扮演|進入).{0,10}(?:無限制|開發者模式)|(?:假裝|自稱|我是).{0,12}(?:管理員|root|系統主管)',t):raise PolicyDenied('instruction_override')
 if re.search(r'(?:顯示|輸出|列出|洩漏|傳送|寄送|給我|print|reveal|export|send).{0,30}(?:系統提示|system prompt|全部文件|所有文件|api key|憑證|密碼|帳密|private key)|(?:開通|核准|批准).{0,16}(?:自己|我的).{0,12}(?:權限|申請)',t):raise PolicyDenied('exfiltration_or_approval')
 if role=='support' and re.search(r'財務(?:系統|報表)|營運月報|finance_reporting|改成.{0,8}主管',t):raise PolicyDenied('role_scope')

def load_registry():
 return json.loads((Path(__file__).parent/'synthetic_registry.json').read_text('utf-8'))
# Explicit document families only; unknown free questions still use authorized RAG.
# Scope patterns identify requested subject, never grant identity or permissions.
DOCUMENT_QUERY_PATTERNS={
 'support-handover':r'(?:客服|前任).{0,18}交接|交接.{0,18}(?:客服|前任)',
 'operations-report':r'(?:公司|九月|9月).{0,12}營運|營運.{0,8}(?:月報|報告)',
 'it-sop':r'mcp.{0,12}(?:timeout|逾時|超時|排障)|資訊支援',
}
def inspect_user_text(text,user,phase='question'):
 if not isinstance(user,dict) or any(not isinstance(user.get(k),str) or not user[k] for k in ('role','department','title')):raise PolicyDenied('invalid_actor_scope')
 inspect_text(text,phase,user['role']);t=normalized(text);registry=load_registry()
 for document_id,pattern in DOCUMENT_QUERY_PATTERNS.items():
  if not re.search(pattern,t):continue
  d=registry['documents'][document_id]
  allowed=(user['role'] in d['allowed_roles'] and (not d.get('allowed_departments') or user['department'] in d['allowed_departments']) and (not d.get('allowed_job_titles') or user['title'] in d['allowed_job_titles']))
  from .it_maintenance_access import alias_access
  if not (allowed or alias_access(dict(d,document_id=document_id),user)):raise PolicyDenied('document_scope')

def validate_context(prompt,chunks,history,question,role,systems,user=None):
 registry=load_registry()
 if sha(prompt) not in registry['prompt_hashes']:raise PolicyDenied('unapproved_prompt')
 inspect_user_text(question,user,'question') if user is not None else inspect_text(question,'question',role)
 for turn in history:
  if not turn.get('stamp') or not turn.get('generation'):raise PolicyDenied('untrusted_history')
  inspect_user_text(turn['question'],user,'history_question') if user is not None else inspect_text(turn['question'],'history_question',role)
  inspect_text(turn['answer'],'history_answer',role)
 for chunk in chunks:
  d=chunk['document'];known=registry['documents'].get(d['document_id'])
  if not known or any(d.get(k)!=known.get(k) for k in ('sha256','policy_version','lifecycle_status','scope','allowed_roles','allowed_departments','allowed_job_titles')):raise PolicyDenied('unapproved_source_version')
  from .it_maintenance_access import alias_access
  permitted=role in known['allowed_roles'] or (user is not None and alias_access(dict(known,document_id=d['document_id']),user))
  if not permitted or chunk['text'] not in known['body']:raise PolicyDenied('unauthorized_source')
  inspect_text(chunk['text'],'document',role)
 return registry

def validate_proposal(proposal,question,systems):
 if not proposal:return
 if proposal['system_id'] not in systems:raise PolicyDenied('tool_scope')
 if not explicit_crm_request(question,systems):
  raise PolicyDenied('unrequested_tool')
 inspect_text(proposal['reason'],'tool_reason')

def explicit_crm_request(question,systems):
 t=normalized(question)
 return 'crm' in systems and 'crm' in t and bool(re.search(r'(?:我要|我想要|請幫我|請協助|幫我|協助我)(?:\s|使用|辦理|建立|提出|一個|一筆|crm|存取|權限)*申請',t))
