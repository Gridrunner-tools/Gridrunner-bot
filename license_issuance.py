"""Automatic Stripe purchase fulfillment with private Neon registry and email client."""
import json, os, secrets, urllib.request
from datetime import datetime, timedelta, timezone
from issue_license import generate_key
from license_registry import _database_url, _driver, LicenseRegistryUnavailable

def issue_product_license(email, product, stripe_session_id, days=365):
    if product not in ('gridrunner', 'limit_orders'): raise ValueError('unsupported product')
    key = generate_key() if product == 'gridrunner' else 'LO-' + secrets.token_hex(6).upper()
    expires = None if not days else datetime.now(timezone.utc) + timedelta(days=days)
    try:
        with _driver().connect(_database_url(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO licenses (license_key,license_type,expires_at,customer_email,stripe_session_id,product) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (stripe_session_id) DO NOTHING RETURNING license_key", (key,'full',expires,email,stripe_session_id,product))
                row=cur.fetchone(); conn.commit()
    except Exception as exc: raise LicenseRegistryUnavailable('License registry write failed') from exc
    if not row: return None
    send_license_email(email,key,product)
    return key

def send_license_email(to, key, product):
    api=os.environ.get('EMAIL_API_KEY'); sender=os.environ.get('EMAIL_FROM')
    if not api or not sender: raise RuntimeError('email client not configured')
    payload=json.dumps({'from':sender,'to':[to],'subject':f'{product} license key','text':f'Your {product} license key: {key}'}).encode()
    req=urllib.request.Request(os.environ.get('EMAIL_API_URL','https://api.resend.com/emails'),payload,{'Authorization':f'Bearer {api}','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10): pass

def handle_stripe_webhook(payload, signature):
    import hmac, hashlib
    secret=os.environ.get('STRIPE_WEBHOOK_SECRET','')
    if not secret or not hmac.compare_digest(hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest(), signature or ''): raise ValueError('invalid webhook signature')
    event=json.loads(payload); obj=event.get('data',{}).get('object',{}); session=event.get('id') or obj.get('id'); email=(obj.get('customer_details') or {}).get('email'); product=obj.get('metadata',{}).get('product','gridrunner')
    return issue_product_license(email, product, session)
