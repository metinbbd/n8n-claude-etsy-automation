"""Kaynak yoklama: SVS kayitlarindaki tum mp4 dosyalarini listeler."""
import json
import sys
import urllib.request

sys.path.insert(0, __import__("os").path.dirname(__file__))
from kesif import svs_files  # noqa: E402

for sid in (31194, 14215, 14473, 14774):
    print("=" * 90, "\nSVS", sid)
    try:
        req = urllib.request.Request(f"https://svs.gsfc.nasa.gov/api/{sid}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print(d.get("title"))
        for f in svs_files(d, []):
            print(f"  {f['w']}x{f['h']}  {f['url']}")
    except Exception as e:  # noqa: BLE001
        print("HATA", e)
