"""Gunluk Etsy satis raporu (SALT OKUMA).

Akis:
  1. n8n'de "Etsy Gunluk Rapor (salt okuma)" is akisini olusturur/gunceller.
     Bu is akisi Etsy'ye SADECE GET istegi atar (urun yayinlama, silme, fiyat
     degistirme, reklam yok) ve alici kisisel bilgilerini n8n icinde atar.
  2. Is akisini tetikler, temizlenmis siparis ozetini alir.
  3. Turkce raporu hazirlar ve n8n'deki Gmail baglantisi ile
     selinmetin13@gmail.com adresine gonderir.

Depo herkese acik oldugu icin rapor icerigi LOGA YAZILMAZ, dosyaya kaydedilmez.
"""
import argparse
import html
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from n8n_common import RaporHatasi, deploy, list_all, secret_path, send_mail, webhook

WORKFLOW_NAME = "Etsy Gunluk Rapor (salt okuma)"
ETSY_CRED_NAME = "Etsy OAuth2"
ETSY = "https://openapi.etsy.com/v3/application"
IST = timezone(timedelta(hours=3))  # Istanbul, yaz/kis saati uygulamasi yok
DAYS_FETCHED = 35

AYLAR_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
            "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


# ------------------------------------------------- Etsy anahtari (x-api-key)

def _set_values(nodes):
    """Set dugumlerindeki sabit degerler: {alan_adi: deger}."""
    values = {}
    for node in nodes:
        if node.get("type") != "n8n-nodes-base.set":
            continue
        p = node.get("parameters", {})
        for a in (p.get("assignments") or {}).get("assignments", []):
            values[a.get("name")] = a.get("value")
        for group in (p.get("values") or {}).values():
            for a in group or []:
                values[a.get("name")] = a.get("value")
    return {k: v for k, v in values.items()
            if isinstance(v, str) and not v.startswith("=")}


def resolve_api_key(value, nodes):
    """x-api-key degerini sabit metne cevirir. Cozulemezse None."""
    if not isinstance(value, str) or not value:
        return None
    if not value.startswith("="):
        return value
    expr = value[1:]
    if re.fullmatch(r"\s*\{\{\s*\$vars\.[A-Za-z0-9_]+\s*\}\}\s*", expr):
        return value  # n8n degiskeni: aynen kullanilabilir
    consts = _set_values(nodes)

    def sub(m):
        refs = re.findall(r"json(?:\.([A-Za-z0-9_]+)|\[['\"]([^'\"]+)['\"]\])", m.group(1))
        if not refs:
            raise KeyError
        name = refs[-1][0] or refs[-1][1]
        return consts[name]

    try:
        return re.sub(r"\{\{(.*?)\}\}", sub, expr)
    except KeyError:
        return None


def find_etsy_setup(workflows, credentials):
    cred_id = next((c["id"] for c in credentials if c.get("name") == ETSY_CRED_NAME), None)
    keys = []
    for wf in workflows:
        if wf.get("name") == WORKFLOW_NAME:
            continue
        nodes = wf.get("nodes", [])
        for node in nodes:
            if node.get("type") != "n8n-nodes-base.httpRequest":
                continue
            cred = (node.get("credentials") or {}).get("oAuth2Api") or {}
            if cred.get("name") == ETSY_CRED_NAME and not cred_id:
                cred_id = cred.get("id")
            for h in (node.get("parameters", {}).get("headerParameters") or {}).get("parameters", []):
                if str(h.get("name", "")).lower() == "x-api-key":
                    k = resolve_api_key(h.get("value"), nodes)
                    if k:
                        keys.append(k)
    env_key = os.environ.get("ETSY_API_KEY", "").strip()
    if env_key:
        keys.insert(0, env_key)
    keys.sort(key=lambda k: ":" not in k)  # keystring:shared_secret formati oncelikli
    return cred_id, (keys[0] if keys else None)


# ------------------------------------------------------ n8n is akisi tanimi

SANITIZE_JS = r"""
// Alici kisisel bilgileri (isim, e-posta, adres, mesaj, alici id) burada atilir.
const shop = $('Etsy magaza').first().json;
const num = (m) => (m && m.divisor ? m.amount / m.divisor : 0);
const seen = new Set();
const orders = [];
let reported = 0;
for (const item of $input.all()) {
  const page = item.json || {};
  reported = Math.max(reported, page.count || 0);
  for (const r of page.results || []) {
    if (seen.has(r.receipt_id)) continue;
    seen.add(r.receipt_id);
    orders.push({
      t: r.created_timestamp || r.create_timestamp,
      status: String(r.status || '').toLowerCase(),
      paid: !!r.is_paid,
      shipped: !!r.is_shipped,
      total: num(r.grandtotal),
      subtotal: num(r.subtotal),
      shipping: num(r.total_shipping_cost),
      discount: num(r.discount_amt),
      refund: (r.refunds || []).reduce((s, x) => s + num(x.amount), 0),
      items: (r.transactions || []).map((t) => ({
        listing_id: t.listing_id,
        title: t.title,
        qty: t.quantity || 0,
        price: num(t.price),
        digital: !!t.is_digital,
      })),
    });
  }
}
return [{ json: {
  shop: {
    name: shop.shop_name,
    currency: shop.currency_code,
    active_listings: shop.listing_active_count,
    favorers: shop.num_favorers,
    review_count: shop.review_count,
    review_average: shop.review_average,
    sold_total: shop.transaction_sold_count,
  },
  reported_count: reported,
  orders,
} }];
"""


def build_workflow(cred_id, api_key):
    etsy_auth = {
        "authentication": "genericCredentialType",
        "genericAuthType": "oAuth2Api",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "x-api-key", "value": api_key}]},
    }
    creds = {"oAuth2Api": {"id": cred_id, "name": ETSY_CRED_NAME}}

    def etsy_get(name, url, pos, extra=None):
        params = {"method": "GET", "url": url, **etsy_auth, "options": {}}
        params.update(extra or {})
        return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)), "name": name,
                "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
                "position": pos, "parameters": params, "credentials": creds}

    hook_data = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "veri-webhook")), "name": "Webhook veri",
        "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0],
        "webhookId": str(uuid.uuid5(uuid.NAMESPACE_URL, secret_path("veri"))),
        "parameters": {"httpMethod": "POST", "path": secret_path("veri"),
                       "responseMode": "responseNode", "options": {}},
    }
    me = etsy_get("Etsy kullanici", f"{ETSY}/users/me", [220, 0])
    shop = etsy_get("Etsy magaza", f"{ETSY}/shops/{{{{ $json.shop_id }}}}", [440, 0])
    shop["parameters"]["url"] = "=" + shop["parameters"]["url"]
    receipts = etsy_get(
        "Etsy siparisler",
        f"={ETSY}/shops/{{{{ $('Etsy kullanici').first().json.shop_id }}}}/receipts",
        [660, 0],
        {
            "sendQuery": True,
            "queryParameters": {"parameters": [
                {"name": "limit", "value": "100"},
                {"name": "min_created",
                 "value": "={{ $('Webhook veri').first().json.body.min_created }}"},
                {"name": "sort_on", "value": "created"},
                {"name": "sort_order", "value": "desc"},
            ]},
            "options": {"pagination": {"pagination": {
                "paginationMode": "updateAParameterInEachRequest",
                "parameters": {"parameters": [
                    {"type": "qs", "name": "offset", "value": "={{ $pageCount * 100 }}"}]},
                "paginationCompleteWhen": "other",
                "completeExpression": "={{ ($response.body.results || []).length < 100 }}",
                "limitPagesFetched": True,
                "maxRequests": 30,
                "requestInterval": 300,
            }}},
        },
    )
    clean = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "temizle")), "name": "Kisisel bilgiyi temizle",
        "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [880, 0],
        "parameters": {"mode": "runOnceForAllItems", "jsCode": SANITIZE_JS.strip()},
    }
    respond_data = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "veri-yanit")), "name": "Ozet dondur",
        "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [1100, 0],
        "parameters": {"respondWith": "firstIncomingItem", "options": {}},
    }
    nodes = [hook_data, me, shop, receipts, clean, respond_data]
    connections = {
        "Webhook veri": {"main": [[{"node": "Etsy kullanici", "type": "main", "index": 0}]]},
        "Etsy kullanici": {"main": [[{"node": "Etsy magaza", "type": "main", "index": 0}]]},
        "Etsy magaza": {"main": [[{"node": "Etsy siparisler", "type": "main", "index": 0}]]},
        "Etsy siparisler": {"main": [[{"node": "Kisisel bilgiyi temizle", "type": "main", "index": 0}]]},
        "Kisisel bilgiyi temizle": {"main": [[{"node": "Ozet dondur", "type": "main", "index": 0}]]},
    }
    return {
        "name": WORKFLOW_NAME,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "timezone": "Europe/Istanbul",
                     # Basarili calismalarin verisi (alici bilgisi iceren ham yanit) n8n'de saklanmasin.
                     "saveDataSuccessExecution": "none"},
    }


def assert_read_only(wf):
    """Guvenlik: Etsy'ye giden her istek GET olmali."""
    for node in wf["nodes"]:
        if node["type"] == "n8n-nodes-base.httpRequest":
            p = node["parameters"]
            if p.get("method", "GET") != "GET" or "openapi.etsy.com" not in p["url"]:
                raise RaporHatasi(f"Guvenlik: {node['name']} salt okuma degil!")
            if p.get("sendBody"):
                raise RaporHatasi(f"Guvenlik: {node['name']} govde gonderiyor!")


# ----------------------------------------------------------------- rapor

def para(x, cur):
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {cur}"


def tarih(d):
    return f"{d.day} {AYLAR_TR[d.month - 1]} {d.year}, {GUNLER_TR[d.weekday()]}"


def is_cancelled(o):
    return o["status"] in ("canceled", "cancelled", "fully refunded")


def summarize(orders, start, end):
    sel = [o for o in orders if start <= o["t"] < end]
    ok = [o for o in sel if not is_cancelled(o)]
    return {
        "orders": len(ok),
        "items": sum(i["qty"] for o in ok for i in o["items"]),
        "revenue": sum(o["total"] for o in ok),
        "cancelled": len(sel) - len(ok),
        "refund": sum(o["refund"] for o in sel),
        "list": ok,
    }


def degisim(now, before):
    if before == 0:
        return "—" if now == 0 else "yeni"
    pct = (now - before) / before * 100
    ok = "▲" if pct > 0 else ("▼" if pct < 0 else "=")
    return f"{ok} %{abs(pct):.0f}"


def top_products(orders, n):
    agg = defaultdict(lambda: {"qty": 0, "rev": 0.0, "title": ""})
    for o in orders:
        for i in o["items"]:
            a = agg[i["listing_id"] or i["title"]]
            a["title"] = i["title"] or "(isimsiz)"
            a["qty"] += i["qty"]
            a["rev"] += i["qty"] * i["price"]
    return sorted(agg.values(), key=lambda a: (-a["qty"], -a["rev"]))[:n]


def build_report(data, now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    shop = data.get("shop") or {}
    cur = shop.get("currency") or ""
    orders = data.get("orders") or []
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ts = lambda d: int(d.timestamp())  # noqa: E731
    day = lambda back: (ts(today - timedelta(days=back)), ts(today - timedelta(days=back - 1)))  # noqa: E731

    y = summarize(orders, *day(1))
    y2 = summarize(orders, *day(2))
    wk = summarize(orders, *day(8))
    l7 = summarize(orders, ts(today - timedelta(days=7)), ts(today))
    p7 = summarize(orders, ts(today - timedelta(days=14)), ts(today - timedelta(days=7)))
    l30 = summarize(orders, ts(today - timedelta(days=30)), ts(today))
    waiting = [o for o in orders if o["paid"] and not o["shipped"] and not is_cancelled(o)
               and any(not i["digital"] for i in o["items"])]
    yday = today - timedelta(days=1)

    subject = (f"Etsy günlük rapor – {yday.day} {AYLAR_TR[yday.month - 1]}: "
               f"{y['orders']} sipariş, {para(y['revenue'], cur)}")

    e = html.escape
    row = lambda *c: "<tr>" + "".join(  # noqa: E731
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{x}</td>" for x in c) + "</tr>"
    th = lambda *c: "<tr>" + "".join(  # noqa: E731
        f"<th style='text-align:left;padding:6px 10px;background:#f6f1ea'>{x}</th>" for x in c) + "</tr>"
    table = "<table style='border-collapse:collapse;width:100%;font-size:14px'>"

    parts = ["<div style='font-family:Arial,sans-serif;max-width:640px;color:#222'>",
             "<h2 style='color:#d5641c;margin-bottom:4px'>Etsy günlük satış raporu</h2>",
             f"<div style='color:#666'>{e(shop.get('name') or '')} · Dün: {tarih(yday)}</div>",
             "<h3>Dün</h3>", table,
             th("", "Dün", "Önceki gün", "Geçen hafta aynı gün"),
             row("Sipariş", y["orders"], f"{y2['orders']} ({degisim(y['orders'], y2['orders'])})",
                 f"{wk['orders']} ({degisim(y['orders'], wk['orders'])})"),
             row("Satılan ürün adedi", y["items"], y2["items"], wk["items"]),
             row("Ciro", para(y["revenue"], cur), para(y2["revenue"], cur), para(wk["revenue"], cur)),
             row("Ortalama sipariş", para(y["revenue"] / y["orders"], cur) if y["orders"] else "—",
                 "", ""),
             "</table>"]

    if y["list"]:
        parts += ["<h3>Dün satılan ürünler</h3>", table, th("Ürün", "Adet", "Tutar")]
        for p in top_products(y["list"], 20):
            parts.append(row(e(p["title"][:90]), p["qty"], para(p["rev"], cur)))
        parts.append("</table>")
    else:
        parts.append("<p>Dün sipariş gelmedi.</p>")

    parts += ["<h3>Dönem özeti</h3>", table, th("", "Sipariş", "Adet", "Ciro", "Değişim"),
              row("Son 7 gün", l7["orders"], l7["items"], para(l7["revenue"], cur),
                  f"önceki 7 güne göre {degisim(l7['revenue'], p7['revenue'])}"),
              row("Önceki 7 gün", p7["orders"], p7["items"], para(p7["revenue"], cur), ""),
              row("Son 30 gün", l30["orders"], l30["items"], para(l30["revenue"], cur), ""),
              "</table>"]

    best = top_products(l30["list"], 5)
    if best:
        parts += ["<h3>Son 30 günün en çok satanları</h3>", table, th("Ürün", "Adet", "Tutar")]
        parts += [row(e(p["title"][:90]), p["qty"], para(p["rev"], cur)) for p in best]
        parts.append("</table>")

    parts += ["<h3>Takip</h3><ul>",
              f"<li>Kargolanmayı bekleyen fiziksel sipariş: <b>{len(waiting)}</b></li>",
              f"<li>Son 7 günde iptal/iade edilen sipariş: {l7['cancelled']}"
              + (f" (iade tutarı {para(l7['refund'], cur)})" if l7["refund"] else "") + "</li>",
              f"<li>Aktif ilan: {shop.get('active_listings', '—')} · Favorileyen: "
              f"{shop.get('favorers', '—')} · Yorum: {shop.get('review_count', '—')}"
              + (f" (ort. {shop['review_average']:.2f})" if shop.get("review_average") else "")
              + "</li></ul>"]
    if data.get("reported_count", 0) > len(orders):
        parts.append("<p style='color:#b00'>Not: Etsy'den siparişlerin hepsi alınamadı; "
                     "rakamlar eksik olabilir.</p>")
    parts.append("<p style='color:#888;font-size:12px'>Tutarlar sipariş toplamıdır (Etsy "
                 "kesintilerinden önce). Bu rapor salt okumadır: mağazada hiçbir şey "
                 "değiştirilmedi. Alıcı kişisel bilgisi içermez.</p></div>")
    return subject, "\n".join(parts)


def error_mail(msg):
    subject = "Etsy günlük rapor ALINAMADI"
    body = ("<div style='font-family:Arial,sans-serif'><h2 style='color:#b00'>Bugünkü Etsy raporu "
            f"hazırlanamadı</h2><p>{html.escape(msg)}</p><p>Etsy bağlantısının süresi dolmuş "
            "olabilir: n8n → Credentials → <b>Etsy OAuth2</b> → yeniden bağlan.</p></div>")
    return subject, body


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-mail", action="store_true", help="Mail gonderme (sadece test)")
    args = ap.parse_args()

    workflows = list_all("/workflows")
    credentials = list_all("/credentials")
    cred_id, api_key = find_etsy_setup(workflows, credentials)
    if not cred_id:
        raise RaporHatasi(f"n8n'de '{ETSY_CRED_NAME}' baglantisi bulunamadi.")
    if not api_key:
        raise RaporHatasi("Etsy x-api-key bulunamadi. GitHub'a ETSY_API_KEY secret'i "
                          "(keystring:shared_secret) ekleyin.")
    print("Etsy baglantisi bulundu. x-api-key formati:",
          "keystring:shared_secret ✓" if ":" in api_key else "SADECE keystring (Etsy reddedebilir)")

    wf = build_workflow(cred_id, api_key)
    assert_read_only(wf)
    deploy(wf, workflows)
    print("n8n is akisi guncel ve aktif.")

    min_created = int((datetime.now(IST) - timedelta(days=DAYS_FETCHED)).timestamp())
    error = None
    try:
        data = webhook(secret_path("veri"), {"min_created": min_created})
        if not isinstance(data, dict) or "orders" not in data:
            raise RaporHatasi("n8n'den beklenmeyen yanit geldi (Etsy hatasi olabilir).")
        shop = data.get("shop") or {}
        print(f"Etsy verisi alindi ({len(data['orders'])} siparis kaydi, son {DAYS_FETCHED} gun; "
              f"magaza bilgisi {'geldi' if shop.get('currency') else 'GELMEDI'}; "
              f"magazada {'daha once satis var' if shop.get('sold_total') else 'henuz satis yok'}).")
        subject, body = build_report(data)
    except RaporHatasi as e:
        error = e
        print(f"HATA: {e}")
        subject, body = error_mail(str(e))

    if args.no_mail:
        print("Test modu: mail gonderilmedi.")
    else:
        send_mail(subject, body, credentials, workflows)
        print("Rapor maili gonderildi.")
    if error:
        raise error


if __name__ == "__main__":
    try:
        main()
    except RaporHatasi as e:
        print(f"HATA: {e}")
        sys.exit(1)
