"""Kaynak yoklama: verilen adreslerin cevabini ozetler (kesif icin)."""
import json
import re
import sys
import urllib.request

URLS = [
    "https://svs.gsfc.nasa.gov/api/search/?search=aurora&limit=50",
    "https://svs.gsfc.nasa.gov/api/search/?search=aurora%20space%20station&limit=50",
    "https://svs.gsfc.nasa.gov/api/search/?search=coronal%20mass%20ejection&limit=50",
    "https://svs.gsfc.nasa.gov/api/search/?search=magnetosphere&limit=50",
    "https://svs.gsfc.nasa.gov/api/search/?search=earth%20at%20night&limit=50",
    "https://eol.jsc.nasa.gov/BeyondThePhotography/CrewEarthObservationsVideos/",
]
for url in URLS:
    print("=" * 100, "\n", url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print("HATA", e)
        continue
    try:
        data = json.loads(body)
        print(json.dumps(data, ensure_ascii=False)[:6000])
    except ValueError:
        links = sorted(set(re.findall(r'href="([^"]+\.(?:mp4|mov|html?|cfm)[^"]*)"', body)))
        print(len(body), "bayt;", len(links), "baglanti")
        print("\n".join(links[:200]))
        print(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))[:3000])
