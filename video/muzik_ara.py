"""Kevin MacLeod (incompetech) listesini tarar, aday parcalari indirip ritim/hareket
olcusu cikarir. Kullanim: python3 video/muzik_ara.py <cikti_klasoru>

Cikti: aday_tablosu.txt (BPM, tur, his, hareket skoru, ritim gucu, sure) ve ham liste.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

import numpy as np

LISTE_URLS = ["https://incompetech.com/music/royalty-free/pieces.json"]
MP3 = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
ISTENEN = re.compile(r"epic|driv|myster|cinemat|ambient|electro|space|sci|inspir|wonder|"
                     r"dream|uplift|build|adventure|heroic|determin|pulse|suspens|tense|"
                     r"majest|grand|soar|rise", re.I)
HIC = re.compile(r"horror|comed|silly|funn|circus|polka|waltz|christmas|holiday|march|"
                 r"blues|country|western|jazz|swing|reggae|ragtime", re.I)


def indir(url, yol):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(yol, "wb") as f:
        f.write(r.read())


def liste_al():
    for u in LISTE_URLS:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            return data
        except Exception as e:  # noqa: BLE001
            print("liste alinamadi:", u, e)
    return None


def alan(p, *adlar, vars=""):
    for a in adlar:
        if a in p and p[a] not in (None, ""):
            return p[a]
    return vars


def analiz(mp3):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", mp3, "-ac", "1", "-ar", "11025",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n, hop = 1024, 256
    if len(x) < n * 8:
        return None
    win = np.hanning(n).astype(np.float32)
    kareler = (len(x) - n) // hop
    mag = np.empty((kareler, n // 2 + 1), dtype=np.float32)
    for i in range(kareler):
        mag[i] = np.abs(np.fft.rfft(x[i * hop:i * hop + n] * win))
    toplam = mag.sum(axis=1) + 1e-9
    akis = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    hareket = float(akis.mean() / toplam.mean())
    e = akis - akis.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    fr = 11025 / hop
    lo, hi = int(fr * 60 / 180), int(fr * 60 / 60)
    lag = lo + int(np.argmax(ac[lo:hi]))
    ritim = float(ac[lag] / (ac[0] + 1e-9))
    rms = np.sqrt((mag ** 2).mean(axis=1))
    # 10 sn'lik pencerelerde ses seviyesi degisimi (dB) -> dinamik hareket
    pen = int(10 * fr)
    db = [20 * np.log10(rms[i:i + pen].mean() + 1e-9) for i in range(0, len(rms) - pen, pen)]
    dinamik = float(np.std(db)) if len(db) > 2 else 0.0
    return {"bpm_olcum": round(60 * fr / lag, 1), "hareket": round(hareket, 3),
            "ritim": round(ritim, 3), "dinamik": round(dinamik, 2),
            "sure": round(len(x) / 11025, 0)}


def main(cikti):
    os.makedirs(cikti, exist_ok=True)
    os.makedirs("/tmp/muzik", exist_ok=True)
    data = liste_al()
    if data is None:
        raise SystemExit("incompetech listesi alinamadi")
    parcalar = data if isinstance(data, list) else next(
        v for v in data.values() if isinstance(v, list))
    print("toplam parca:", len(parcalar))
    print("ornek kayit:", json.dumps(parcalar[0], ensure_ascii=False)[:600])
    open(f"{cikti}/ham_ornek.json", "w").write(json.dumps(parcalar[:3], ensure_ascii=False, indent=1))
    adaylar = []
    for p in parcalar:
        ad = alan(p, "title", "name", "Title")
        metin = " ".join(str(alan(p, k)) for k in ("genre", "feel", "feels", "category",
                                                   "genres", "instruments", "description"))
        bpm = alan(p, "bpm", "BPM", vars=0)
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            bpm = 0
        if not ad or HIC.search(metin) or not ISTENEN.search(metin):
            continue
        if bpm and not 80 <= bpm <= 150:
            continue
        adaylar.append((ad, bpm, metin))
    print("aday sayisi:", len(adaylar))
    satirlar = []
    for ad, bpm, metin in adaylar[:140]:
        yol = f"/tmp/muzik/{re.sub(r'[^A-Za-z0-9]+', '_', ad)}.mp3"
        try:
            indir(MP3 + urllib.parse.quote(ad) + ".mp3", yol)
            a = analiz(yol)
        except Exception as e:  # noqa: BLE001
            print("atlandi:", ad, str(e)[:80])
            continue
        finally:
            if os.path.exists(yol):
                os.remove(yol)
        if not a or a["sure"] < 150:
            continue
        satirlar.append((ad, bpm, metin[:60], a))
        print(f"{ad:32s} bpm={bpm:5.0f} olcum={a['bpm_olcum']:6.1f} hareket={a['hareket']:.3f} "
              f"ritim={a['ritim']:.3f} dinamik={a['dinamik']:.1f} sure={a['sure']:.0f}  {metin[:50]}",
              flush=True)
    satirlar.sort(key=lambda s: -(s[3]["hareket"] + s[3]["ritim"]))
    with open(f"{cikti}/aday_tablosu.txt", "w") as f:
        for ad, bpm, metin, a in satirlar:
            f.write(f"{ad:32s} bpm={bpm:5.0f} olcum={a['bpm_olcum']:6.1f} hareket={a['hareket']:.3f} "
                    f"ritim={a['ritim']:.3f} dinamik={a['dinamik']:.1f} sure={a['sure']:.0f}  {metin}\n")


if __name__ == "__main__":
    main(sys.argv[1])
