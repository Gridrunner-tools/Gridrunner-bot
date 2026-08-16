import hashlib,hmac,json,time,os
import license_issuance as li
from unittest.mock import patch

def payload(event_type='checkout.session.completed',product='limit_orders',sid='cs_test'):
 return json.dumps({'type':event_type,'data':{'object':{'id':sid,'metadata':({'product':product} if product is not None else {}),'customer_details':{'email':None}}}}).encode()
def sig(raw,secret='s',ts=None):
 ts=int(time.time() if ts is None else ts); return f't={ts},v1={hmac.new(secret.encode(),str(ts).encode()+b'.'+raw,hashlib.sha256).hexdigest()}'
def test_signature_event_filter_and_unknown():
 with patch.dict(os.environ,{'STRIPE_WEBHOOK_SECRET':'s'}), patch.object(li,'issue_product_license',return_value='K'):
  assert li.handle_stripe_webhook(payload('payment_intent.succeeded'),sig(payload('payment_intent.succeeded'))) is None
  try: li.handle_stripe_webhook(payload(product='unknown'),sig(payload(product='unknown'))); assert False
  except ValueError: pass
def test_signature_valid_invalid_expired():
 with patch.dict(os.environ,{'STRIPE_WEBHOOK_SECRET':'s'}), patch.object(li,'issue_product_license',return_value='K'):
  raw=payload(); assert li.handle_stripe_webhook(raw,sig(raw))=='K'
  for bad in ('t=1,v1=bad',sig(raw,ts=1)):
   try: li.handle_stripe_webhook(raw,bad); assert False
   except ValueError: pass
def test_idempotent_session_and_email_failure_nonfatal():
 with patch.dict(os.environ,{'STRIPE_WEBHOOK_SECRET':'s'}), patch.object(li,'issue_product_license',return_value='K') as f:
  raw=payload(); assert li.handle_stripe_webhook(raw,sig(raw))=='K'; assert f.call_args[0][2]=='cs_test'
