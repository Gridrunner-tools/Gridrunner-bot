"""Stripe fulfillment: product-scoped Neon licenses and email delivery."""
import hashlib,hmac,json,logging,os,secrets,sys,urllib.request,time
from datetime import datetime,timedelta,timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
from issue_license import generate_key
from license_registry import _database_url,_driver,LicenseRegistryUnavailable
log=logging.getLogger(__name__)
PRODUCTS={'gridrunner','limit_orders'}
def issue_product_license(email,product,stripe_session_id,days=365):
 if product not in PRODUCTS or not stripe_session_id: raise ValueError('invalid product or session')
 key=generate_key() if product=='gridrunner' else 'LO-'+secrets.token_hex(6).upper(); exp=None if not days else datetime.now(timezone.utc)+timedelta(days=days)
 try:
  with _driver().connect(_database_url(),connect_timeout=10) as c:
   with c.cursor() as cur:
    cur.execute("INSERT INTO licenses (license_key,license_type,expires_at,customer_email,stripe_session_id,product) VALUES (%s,'full',%s,%s,%s,%s) ON CONFLICT (stripe_session_id) DO NOTHING RETURNING license_key",(key,exp,email,stripe_session_id,product)); row=cur.fetchone(); c.commit()
 except Exception as exc: raise LicenseRegistryUnavailable('License registry write failed') from exc
 if not row:return None
 try:
  if email: send_license_email(email,row[0],product)
 except Exception: log.warning('License email delivery failed')
 return row[0]
def send_license_email(to,key,product):
 api=os.environ.get('EMAIL_API_KEY'); sender=os.environ.get('EMAIL_FROM')
 if not api or not sender:return
 body=json.dumps({'from':sender,'to':[to],'subject':f'{product} license key','text':f'Your {product} license key: {key}'}).encode(); req=urllib.request.Request(os.environ.get('EMAIL_API_URL','https://api.resend.com/emails'),body,{'Authorization':f'Bearer {api}','Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=10): pass
def handle_stripe_webhook(payload,signature):
 secret=os.environ.get('STRIPE_WEBHOOK_SECRET',''); raw=payload if isinstance(payload,bytes) else payload.encode(); parts={}
 for part in (signature or '').split(','):
  if '=' in part: k,v=part.strip().split('=',1); parts.setdefault(k,[]).append(v)
 ts=int(parts.get('t',['0'])[0]); sigs=parts.get('v1',[])
 if not secret or abs(time.time()-ts)>300: raise ValueError('invalid or expired webhook signature')
 expected=hmac.new(secret.encode(),str(ts).encode()+b'.'+raw,hashlib.sha256).hexdigest()
 if not any(hmac.compare_digest(expected,x) for x in sigs): raise ValueError('invalid webhook signature')
 event=json.loads(raw); typ=event.get('type')
 if typ!='checkout.session.completed': return None
 obj=event.get('data',{}).get('object',{}); product=(obj.get('metadata') or {}).get('product')
 if product not in PRODUCTS: raise ValueError('unknown product')
 return issue_product_license((obj.get('customer_details') or {}).get('email'),product,obj.get('id'))
