"""Kaynak yoklama: SVS kayitlarindaki medya dosyalarini listeler.
Numaralar video/yokla_idler.txt dosyasindan (satir basina bir tane) okunur.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from kesif import svs_files  # noqa: E402

LISTE = os.path.join(os.path.dirname(__file__), "yokla_idler.txt")
ARA = os.path.join(os.path.dirname(__file__), "yokla_ara.txt")
ids = [int(x) for x in open(LISTE).read().split() if x.strip().isdigit()] if os.path.exists(LISTE) \
    else [31194, 14215, 14473, 14774]
MEDYA = re.compile(r"\.(mp4|mov|webm|m4v|mpg|mpeg|png|jpg|jpeg|tif|tiff|exr)$", re.I)


def tum_adresler(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "url" and isinstance(v, str) and MEDYA.search(v):
                out.append((v, obj.get("width"), obj.get("height")))
            tum_adresler(v, out)
    elif isinstance(obj, list):
        for v in obj:
            tum_adresler(v, out)
    return out




def cek(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


# yokla_ara.txt: satir basina "svs|sorgu" veya "lib|sorgu" (kutuphane); sonuc listesi basilir
if os.path.exists(ARA):
    for satir in open(ARA):
        satir = satir.strip()
        if not satir:
            continue
        kaynak, _, sorgu = satir.partition("|")
        print("=" * 90, f"\nARAMA {kaynak} {sorgu}")
        try:
            if kaynak == "svs":
                q = urllib.parse.urlencode({"search": sorgu, "limit": 40})
                for r in cek(f"https://svs.gsfc.nasa.gov/api/search/?{q}").get("results", []):
                    print(f"  {r['id']} | {r.get('result_type')} | {(r.get('release_date') or '')[:10]} | {r['title'][:90]}")
            else:
                tur = "image" if kaynak == "libimg" else "video"
                q = urllib.parse.urlencode({"q": sorgu, "media_type": tur, "page_size": 60})
                for it in cek(f"https://images-api.nasa.gov/search?{q}")["collection"]["items"]:
                    d = it["data"][0]
                    print(f"  {d['nasa_id'][:70]} | {(d.get('date_created') or '')[:10]} | {d.get('title', '')[:80]}")
        except Exception as e:  # noqa: BLE001
            print("HATA", e)

for sid in ids:
    print("=" * 90, "\nSVS", sid)
    try:
        req = urllib.request.Request(f"https://svs.gsfc.nasa.gov/api/{sid}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print(d.get("title"), "|", (d.get("release_date") or "")[:10])
        mp4 = svs_files(d, [])
        for f in mp4:
            print(f"  MP4 {f['w']}x{f['h']}  {f['url']}")
        if not mp4:
            digerleri = tum_adresler(d, [])
            print("  mp4 yok; diger medya:", len(digerleri))
            for u, w, h in digerleri[:12]:
                print(f"  ? {w}x{h} {u}")
    except Exception as e:  # noqa: BLE001
        print("HATA", e)
