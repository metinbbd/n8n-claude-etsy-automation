"""Kevin MacLeod (incompetech) listesini tarar, aday parcalari indirip ritim/hareket
olcusu cikarir. Kullanim: python3 video/muzik_ara.py <cikti_klasoru>

Cikti: tum_liste.tsv (butun katalog), aday_tablosu.txt (olculen adaylar, hareket + ritim sirali).
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

LISTE = "https://incompetech.com/music/royalty-free/pieces.json"
MP3 = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
UZAY = re.compile(r"galax|star|space|cosm|orbit|nebula|univers|stellar|astro|moon|solar|planet|"
                  r"aurora|void|infinit|ascen|horizon|voyage|odyssey|journey|frontier|"
                  r"discover|wonder|awe|eternal|celest|heaven|drift|float|sky|ether", re.I)
ISTENEN = re.compile(r"epic|myster|mystic|uplift|inspir|majest|driv|groov|intense|suspens|"
                     r"calm|relax|dark|eerie", re.I)
HIC = re.compile(r"humor|bouncy|silly|funn|circus|polka|waltz|christmas|holiday|march|"
                 r"blues|country|western|jazz|swing|reggae|ragtime|aggress|8.?bit|medieval|"
                 r"ren faire|tavern|lullab|sad|somber|romant|sexy|funk", re.I)


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = r.read()
    return d if binary else json.loads(d)


def saniye(s):
    try:
        p = [int(x) for x in str(s).split(":")]
        return sum(v * 60 ** i for i, v in enumerate(reversed(p)))
    except ValueError:
        return 0


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
    pen = int(10 * fr)  # 10 sn'lik pencerelerde seviye degisimi (dB) -> dinamik hareket
    db = [20 * np.log10(rms[i:i + pen].mean() + 1e-9) for i in range(0, len(rms) - pen, pen)]
    dinamik = float(np.std(db)) if len(db) > 2 else 0.0
    return {"bpm_olcum": round(60 * fr / lag, 1), "hareket": round(hareket, 3),
            "ritim": round(ritim, 3), "dinamik": round(dinamik, 2),
            "sure": round(len(x) / 11025, 0)}


def isle(p):
    ad, dosya = p["title"], p["filename"]
    yol = f"/tmp/muzik/{re.sub(r'[^A-Za-z0-9]+', '_', dosya)}"
    try:
        open(yol, "wb").write(get(MP3 + urllib.parse.quote(dosya), binary=True))
        a = analiz(yol)
    except Exception as e:  # noqa: BLE001
        print("atlandi:", ad, str(e)[:80], flush=True)
        return None
    finally:
        if os.path.exists(yol):
            os.remove(yol)
    if not a or a["sure"] < 150:
        return None
    satir = (f"{ad:34s} dosya_ayni={dosya == ad + '.mp3'!s:5s} bpm={str(p.get('bpm') or '?'):>4s} "
             f"olcum={a['bpm_olcum']:6.1f} hareket={a['hareket']:.3f} ritim={a['ritim']:.3f} "
             f"dinamik={a['dinamik']:5.1f} sure={a['sure']:.0f}  g{p.get('genre')} "
             f"{str(p.get('feel') or '')[:44]} | {str(p.get('instruments') or '')[:40]}")
    print(satir, flush=True)
    return (a["hareket"] + a["ritim"], satir)


def main(cikti):
    os.makedirs(cikti, exist_ok=True)
    os.makedirs("/tmp/muzik", exist_ok=True)
    data = get(LISTE)
    parcalar = data if isinstance(data, list) else next(
        v for v in data.values() if isinstance(v, list))
    print("toplam parca:", len(parcalar))
    with open(f"{cikti}/tum_liste.tsv", "w") as f:
        for p in parcalar:
            f.write("\t".join(str(p.get(k, "")).replace("\t", " ").replace("\n", " ")[:90] for k in (
                "title", "filename", "length", "bpm", "genre", "feel", "instruments",
                "description")) + "\n")
    adaylar = []
    for p in parcalar:
        metin = f"{p.get('feel') or ''} {p.get('instruments') or ''}"
        if saniye(p.get("length")) < 150 or not p.get("filename"):
            continue
        uzay = bool(UZAY.search(p["title"]))
        if HIC.search(metin) and not uzay:
            continue
        if uzay or ISTENEN.search(metin):
            adaylar.append(p)
    print("aday sayisi:", len(adaylar), flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        sonuc = [r for r in ex.map(isle, adaylar[:420]) if r]
    sonuc.sort(key=lambda s: -s[0])
    with open(f"{cikti}/aday_tablosu.txt", "w") as f:
        f.writelines(s + "\n" for _, s in sonuc)


if __name__ == "__main__":
    main(sys.argv[1])
