"""n8n hesabini SALT OKUMA olarak kontrol eder.

Depo herkese acik oldugu icin loglara sadece isimler/tipler yazilir;
hicbir anahtar, token veya musteri bilgisi yazdirilmaz.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("N8N_BASE_URL", "https://metinbbd.app.n8n.cloud").rstrip("/")
KEY = os.environ.get("N8N_API_KEY", "")


def api(path):
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}",
        headers={"X-N8N-API-KEY": KEY, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    if not KEY:
        print("HATA: N8N_API_KEY secret'i bulunamadi.")
        sys.exit(1)
    status, data = api("/workflows?limit=250")
    print(f"n8n API /workflows -> HTTP {status}")
    if status != 200:
        print("API anahtari gecersiz olabilir ya da n8n adresi yanlis.")
        sys.exit(1)
    for wf in data.get("data", []):
        print(f"\n- Is akisi: {wf.get('name')!r} (aktif={wf.get('active')})")
        for node in wf.get("nodes", []):
            creds = {k: v.get("name") for k, v in (node.get("credentials") or {}).items()}
            params = json.dumps(node.get("parameters", {})).lower()
            flags = []
            if "x-api-key" in params:
                flags.append("x-api-key basligi var")
            if "openapi.etsy.com" in params:
                flags.append("Etsy API cagrisi")
            print(f"    * {node.get('type')}  kimlik={creds or '-'}  {' | '.join(flags)}")
    status, data = api("/credentials?limit=250")
    print(f"\nn8n API /credentials -> HTTP {status}")
    if status == 200:
        for c in data.get("data", []):
            print(f"    * {c.get('name')!r} tip={c.get('type')}")


if __name__ == "__main__":
    main()
