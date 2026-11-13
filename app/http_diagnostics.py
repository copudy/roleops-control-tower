"""Sanitized provider diagnostics; response bodies and headers are never persisted."""
import hashlib,json,re

def summarize_http_error(error):
 raw=error.read(16385)
 result={'http_status':error.code,'error_body_sha256':hashlib.sha256(raw).hexdigest(),
         'body_truncated':len(raw)>16384}
 try:detail=json.loads(raw[:16384]).get('error',{})
 except (ValueError,UnicodeError,AttributeError):detail={}
 if not isinstance(detail,dict):detail={}
 for name in ('code','type','message'):
  value=detail.get(name)
  if isinstance(value,str):
   value=re.sub(r'gsk_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+|Bearer\s+\S+','[REDACTED]',value)
   result['provider_'+name]=value[:400]
 result['has_failed_generation']='failed_generation' in detail
 # Never store request headers, Key, or failed_generation contents.
 return result

def tool_intent(question):
 """Conservative distinction for the next control test, not general intent proof."""
 q=question.lower().strip()
 if re.search(r'整理.{0,8}條件|有哪些條件|如何申請|誰.{0,4}核准|申請流程',q):return 'guidance'
 if 'crm' in q and re.search(r'請協助建立|請幫我建立|幫我建立|建立.{0,12}待核准申請',q):return 'create_proposal'
 return 'clarify_or_guidance'
