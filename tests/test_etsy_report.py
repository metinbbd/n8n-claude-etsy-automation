import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("N8N_API_KEY", "test-key")
import etsy_report as er  # noqa: E402
import n8n_common  # noqa: E402


NOW = datetime(2026, 10, 8, 9, 5, tzinfo=er.IST)
PII = ["Ayse Yilmaz", "ayse@example.com", "Ataturk Cad", "Kadikoy", "buyer note", "987654"]


def money(x):
    return {"amount": int(round(x * 100)), "divisor": 100, "currency_code": "USD"}


def receipt(rid, days_ago, total, title="Map", qty=1, status="paid", digital=False, shipped=False):
    t = int((NOW - timedelta(days=days_ago)).timestamp())
    return {
        "receipt_id": rid, "buyer_user_id": 987654, "buyer_email": "ayse@example.com",
        "name": "Ayse Yilmaz", "first_line": "Ataturk Cad 1", "city": "Kadikoy",
        "message_from_buyer": "buyer note", "status": status, "is_paid": True,
        "is_shipped": shipped, "created_timestamp": t, "grandtotal": money(total),
        "subtotal": money(total), "total_shipping_cost": money(0), "discount_amt": money(0),
        "refunds": [], "transactions": [{"listing_id": hash(title) % 1000, "title": title,
                                         "quantity": qty, "price": money(total / qty),
                                         "is_digital": digital}],
    }


def run_sanitize(pages, shop):
    js = (
        "const pages = %s; const shopObj = %s;"
        "const $input = { all: () => pages.map((p) => ({ json: p })) };"
        "const $ = () => ({ first: () => ({ json: shopObj }) });"
        "const out = (() => { %s })();"
        "console.log(JSON.stringify(out[0].json));"
    ) % (json.dumps(pages), json.dumps(shop), er.SANITIZE_JS)
    return json.loads(subprocess.check_output(["node", "-e", js]))


class Test(unittest.TestCase):
    def setUp(self):
        self.shop = {"shop_name": "Avenza", "currency_code": "USD", "listing_active_count": 40,
                     "num_favorers": 120, "review_count": 9, "review_average": 4.9,
                     "transaction_sold_count": 300, "email": "owner@example.com"}
        self.pages = [{"count": 4, "results": [
            receipt(1, 0.1, 20.0, "Today map"),          # bugun 06:41 (dun degil)
            receipt(2, 0.8, 15.5, "Istanbul Map", 2),    # dun 13:53
            receipt(3, 1.5, 30.0, "Paris Map", digital=True),  # 2 gun once
            receipt(4, 1.2, 99.0, "Cancelled", status="canceled"),
        ]}, {"count": 4, "results": [receipt(2, 0.8, 15.5, "Istanbul Map", 2)]}]

    def test_sanitize_removes_pii(self):
        out = run_sanitize(self.pages, self.shop)
        dump = json.dumps(out)
        for p in PII + ["owner@example.com", "receipt_id"]:
            self.assertNotIn(p, dump)
        self.assertEqual(len(out["orders"]), 4)  # tekrar eden siparis atildi

    def test_report(self):
        data = run_sanitize(self.pages, self.shop)
        subject, body = er.build_report(data, now=NOW)
        self.assertIn("1 sipariş", subject)
        self.assertIn("15,50 USD", subject)
        self.assertIn("Istanbul Map", body)
        self.assertNotIn("Cancelled", body.split("Dönem")[0].split("Dün satılan")[1])
        for p in PII:
            self.assertNotIn(p, body)
        with open(os.environ.get("REPORT_PREVIEW", "/dev/null"), "w") as f:
            f.write(body)

    def test_workflow_is_read_only(self):
        wf = er.build_workflow("1", "key:secret")
        er.assert_read_only(wf)
        mailer = n8n_common.build_mailer({"id": "2", "name": "Gmail"})
        mail = next(n for n in mailer["nodes"] if n["type"] == "n8n-nodes-base.gmail")
        self.assertEqual(mail["parameters"]["sendTo"], "selinmetin13@gmail.com")
        wf["nodes"][1]["parameters"]["method"] = "DELETE"
        with self.assertRaises(er.RaporHatasi):
            er.assert_read_only(wf)

    def test_resolve_api_key(self):
        nodes = [{"type": "n8n-nodes-base.set", "parameters": {"assignments": {"assignments": [
            {"name": "keystring", "value": "abc"}, {"name": "secret", "value": "xyz"}]}}}]
        self.assertEqual(er.resolve_api_key("abc:xyz", nodes), "abc:xyz")
        self.assertEqual(er.resolve_api_key(
            "={{ $('Config').item.json.keystring }}:{{ $json[\"secret\"] }}", nodes), "abc:xyz")
        self.assertIsNone(er.resolve_api_key("={{ $json.unknown }}", nodes))


if __name__ == "__main__":
    unittest.main()
