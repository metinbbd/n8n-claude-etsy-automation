"""n8n API yardimcilari ve ortak rapor maili (Gmail, n8n uzerinden).

Mail alicisi n8n is akisinda SABIT yazilidir; disaridan degistirilemez.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("N8N_BASE_URL", "https://metinbbd.app.n8n.cloud").rstrip("/")
API_KEY = os.environ.get("N8N_API_KEY", "")
MAIL_TO = "selinmetin13@gmail.com"
MAILER_NAME = "Rapor maili (Gmail)"


class RaporHatasi(Exception):
    pass


def n8n(method, path, body=None):
    if not API_KEY:
        raise RaporHatasi("N8N_API_KEY secret'i tanimli degil.")
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"X-N8N-API-KEY": API_KEY, "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RaporHatasi(f"n8n API {method} {path.split('?')[0]} -> HTTP {e.code}: {detail}")


def list_all(path):
    items, cursor = [], None
    while True:
        sep = "&" if "?" in path else "?"
        data = n8n("GET", f"{path}{sep}limit=250" + (f"&cursor={cursor}" if cursor else ""))
        items += data.get("data", [])
        cursor = data.get("nextCursor")
        if not cursor:
            return items


def secret_path(kind):
    """API anahtarindan turetilen, tahmin edilemeyen webhook yolu (loga yazilmaz)."""
    digest = hashlib.sha256(f"etsy-rapor:{kind}:{API_KEY}".encode()).hexdigest()[:40]
    return f"etsy-rapor-{kind}-{digest}"


def webhook(path, payload, attempts=6):
    url = f"{BASE}/webhook/{path}"
    for i in range(attempts):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            # Yeni aktiflesen webhook birkac saniye 404 verebilir.
            if e.code == 404 and i < attempts - 1:
                time.sleep(5)
                continue
            detail = e.read().decode(errors="replace")[:300]
            raise RaporHatasi(f"n8n is akisi HTTP {e.code} dondu: {detail}")


def node_id(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def deploy(wf, workflows=None):
    """Ayni isimli is akisini gunceller (yoksa olusturur) ve aktiflestirir."""
    workflows = workflows if workflows is not None else list_all("/workflows")
    existing = [w for w in workflows if w.get("name") == wf["name"]]
    if existing:
        wf_id = existing[0]["id"]
        n8n("PUT", f"/workflows/{wf_id}", wf)
    else:
        wf_id = n8n("POST", "/workflows", wf)["id"]
    n8n("POST", f"/workflows/{wf_id}/activate")
    return wf_id


def build_mailer(gmail_cred):
    return {
        "name": MAILER_NAME,
        "nodes": [
            {"id": node_id("mail-webhook"), "name": "Webhook mail",
             "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0],
             "webhookId": node_id(secret_path("mail")),
             "parameters": {"httpMethod": "POST", "path": secret_path("mail"),
                            "responseMode": "responseNode", "options": {}}},
            {"id": node_id("gmail"), "name": "Gmail gonder",
             "type": "n8n-nodes-base.gmail", "typeVersion": 2.1, "position": [220, 0],
             "parameters": {"resource": "message", "operation": "send",
                            "sendTo": MAIL_TO,  # alici sabit
                            "subject": "={{ $json.body.subject }}",
                            "emailType": "html",
                            "message": "={{ $json.body.html }}",
                            "options": {"appendAttribution": False}},
             "credentials": {"gmailOAuth2": {"id": gmail_cred["id"], "name": gmail_cred["name"]}}},
            {"id": node_id("mail-yanit"), "name": "Mail tamam",
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [440, 0],
             "parameters": {"respondWith": "json", "responseBody": '{"ok": true}', "options": {}}},
        ],
        "connections": {
            "Webhook mail": {"main": [[{"node": "Gmail gonder", "type": "main", "index": 0}]]},
            "Gmail gonder": {"main": [[{"node": "Mail tamam", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1", "timezone": "Europe/Istanbul",
                     "saveDataSuccessExecution": "none"},
    }


GMAIL_EKSIK = ("n8n'de Gmail baglantisi yok. n8n → Credentials → Add credential → "
               "'Gmail OAuth2 API' → Sign in with Google → Save.")


def gmail_credential(credentials=None):
    credentials = credentials if credentials is not None else list_all("/credentials")
    return next((c for c in credentials if c.get("type") == "gmailOAuth2"), None)


def send_mail(subject, html_body, credentials=None, workflows=None):
    """Mail is akisini guncel tutar ve maili gonderir. Gmail yoksa RaporHatasi."""
    gmail = gmail_credential(credentials)
    if not gmail:
        raise RaporHatasi(GMAIL_EKSIK)
    deploy(build_mailer(gmail), workflows)
    webhook(secret_path("mail"), {"subject": subject, "html": html_body})
