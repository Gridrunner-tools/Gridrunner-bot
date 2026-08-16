import hashlib,hmac,json,time
import pytest
import license_issuance as li

def payload(event_type='checkout.session.completed',product='limit_orders',sid='cs_test'):
 return json.dumps({'type':event_type,'data':{'object':{'id':sid,'metadata':({'product':product} if product is not None else {}),'customer_details':{'email':None}}}}).encode()
def sig(raw,secret='s',ts=None):
 ts=int(time.time() if ts is None else ts); return f't={ts},v1={hmac.new(secret.encode(),str(ts).encode()+b"."+raw,hashlib.sha256).hexdigest()}'
def test_signature_event_filter_and_unknown(monkeypatch):
 monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','s'); monkeypatch.setattr(li,'issue_product_license',lambda *a: 'K')
 assert li.handle_stripe_webhook(payload('payment_intent.succeeded'),sig(payload('payment_intent.succeeded'))) is None
 with pytest.raises(ValueError): li.handle_stripe_webhook(payload(product='unknown'),sig(payload(product='unknown')))
def test_signature_valid_invalid_expired(monkeypatch):
 monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','s'); monkeypatch.setattr(li,'issue_product_license',lambda *a:'K'); raw=payload()
 assert li.handle_stripe_webhook(raw,sig(raw))=='K'
 with pytest.raises(ValueError): li.handle_stripe_webhook(raw,'t=1,v1=bad')
 with pytest.raises(ValueError): li.handle_stripe_webhook(raw,sig(raw,ts=1))
def test_idempotent_and_email_failure_nonfatal(monkeypatch):
 monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','s'); calls=[]
 monkeypatch.setattr(li,'issue_product_license',lambda *a: calls.append(a) or 'K'); raw=payload(); assert li.handle_stripe_webhook(raw,sig(raw))=='K'; assert li.handle_stripe_webhook(raw,sig(raw))=='K'; assert calls[0][2]=='cs_test'
