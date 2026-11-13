import base64,hashlib,hmac,json,secrets,time

def password_hash(password,salt=None):
    if len(password)<10 or len(password)>256:raise ValueError('密碼需為 10–256 個字元。')
    salt=salt or secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
    return {'salt':salt,'hash':digest}
def verify_password(password,stored):
    try:return hmac.compare_digest(password_hash(password,stored['salt'])['hash'],stored['hash'])
    except (ValueError,KeyError):return False
def sign_actor(user_id,secret,trace_id):
    data=base64.urlsafe_b64encode(json.dumps({'sub':user_id,'exp':int(time.time())+30,'trace_id':trace_id}).encode()).decode()
    return data+'.'+hmac.new(secret.encode(),data.encode(),hashlib.sha256).hexdigest()
def verify_actor(token,secret):
    data,sig=token.split('.',1)
    if not hmac.compare_digest(sig,hmac.new(secret.encode(),data.encode(),hashlib.sha256).hexdigest()):raise ValueError('Invalid service identity')
    claims=json.loads(base64.urlsafe_b64decode(data))
    if claims['exp']<time.time():raise ValueError('Expired service identity')
    return claims
