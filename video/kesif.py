"""Aday klip kesfi: NASA Image & Video Library'de arar, her aday icin
zaman damgali kare tablosu (contact sheet) uretir. Kareler insan/yazi/logo
kontrolu icin elle incelenir.

Kullanim: python3 video/kesif.py video/projeler/<proje>/kesif.json <cikti_klasoru>
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

API = "https://images-api.nasa.gov"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
# Basliginda/aciklamasinda bunlar gecen videolar buyuk ihtimalle insan iceriyor.
PEOPLE = re.compile(
    r"\b(interview|briefing|conference|press|panel|talks?|speaks?|explains?|scientists?|"
    r"astronauts?|engineers?|students?|lecture|webinar|q&a|live shots?|reporter|host|"
    r"administrator|ceremony|crew|family|kids|employees?|team members?|sound ?bites?|"
    r"social media|town hall|podcast|testimony|hearing)\b", re.I)


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


STRONG_PEOPLE = re.compile(r"\b(interview|briefing|conference|press|panel|webinar|q&a|"
                           r"town hall|testimony|hearing|ceremony|live shots?|podcast|"
                           r"this week @nasa|nasa science live|space to ground)\b", re.I)


def search(query, pages=2):
    items = []
    for page in range(1, pages + 1):
        q = urllib.parse.urlencode({"q": query, "media_type": "video", "page": page,
                                    "page_size": 100})
        data = get_json(f"{API}/search?{q}")["collection"]
        items += data.get("items", [])
        if len(data.get("items", [])) < 100:
            break
    return items


def probe(path_or_url):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate:format=duration", "-of", "json",
               path_or_url]).stdout
    d = json.loads(out)
    s = (d.get("streams") or [{}])[0]
    return {"w": s.get("width"), "h": s.get("height"), "fps": s.get("r_frame_rate"),
            "sure": round(float(d.get("format", {}).get("duration") or 0), 1)}


def sheet(video, dest, duration, n=24):
    step = max(duration / n, 0.5)
    vf = (f"fps=1/{step:.3f},scale=320:180:force_original_aspect_ratio=decrease,"
          "pad=320:180:(ow-iw)/2:(oh-ih)/2,"
          f"drawtext=fontfile={FONT}:text='%{{pts\\:hms}}':x=4:y=4:fontsize=16:"
          "fontcolor=yellow:box=1:boxcolor=black@0.6,tile=6x4")
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", vf, "-frames:v", "1",
         "-q:v", "5", dest])


SVS = "https://svs.gsfc.nasa.gov/api"


def svs_files(obj, found):
    """SVS detay JSON'undaki tum mp4 dosyalarini toplar."""
    if isinstance(obj, dict):
        url = obj.get("url") or ""
        if isinstance(url, str) and url.lower().endswith(".mp4"):
            found.append({"url": url, "w": obj.get("width") or 0, "h": obj.get("height") or 0,
                          "boyut": obj.get("filesize") or obj.get("size") or 0})
        for v in obj.values():
            svs_files(v, found)
    elif isinstance(obj, list):
        for v in obj:
            svs_files(v, found)
    return found


def svs_candidates(cfg):
    out, seen = [], set()
    for query in cfg.get("svs_aramalari", []):
        q = urllib.parse.urlencode({"search": query, "limit": cfg.get("svs_limit", 30)})
        for r in get_json(f"{SVS}/search/?{q}").get("results", []):
            if r["id"] in seen or r.get("result_type") in ("Gallery",):
                continue
            seen.add(r["id"])
            out.append({"id": f"svs{r['id']}", "svs": r["id"], "baslik": r["title"],
                        "tur": r.get("result_type"), "tarih": (r.get("release_date") or "")[:10],
                        "aciklama": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                                                             r.get("description") or ""))[:400],
                        "sorgu": query,
                        "insan_suphesi": bool(PEOPLE.search(r["title"] + " " + (r.get("description") or "")))})
    return out


def svs_prepare(c):
    detail = get_json(f"{SVS}/{c['svs']}")
    credits = detail.get("credits") or []
    c["krediler"] = [f"{x.get('role', '')}: {x.get('person', {}).get('name', '') if isinstance(x.get('person'), dict) else x.get('person', '')}"
                     for x in credits if isinstance(x, dict)][:12]
    files = [f for f in svs_files(detail, []) if f["w"]]
    if not files:
        return None
    files.sort(key=lambda f: f["w"])
    small = next((f for f in files if f["w"] >= 480), files[0])
    best = [f for f in files if f["w"] <= 1920] or files[:1]
    c["kaynak"] = {"url": best[-1]["url"], "w": best[-1]["w"], "h": best[-1]["h"]}
    c["dosya_sayisi"] = len(files)
    return small["url"]


def main(config_path, out_dir):
    cfg = json.load(open(config_path))
    os.makedirs(f"{out_dir}/tablolar", exist_ok=True)
    seen, cands = set(), []
    for query in cfg.get("aramalar", []):
        for it in search(query):
            d = it["data"][0]
            nid = d["nasa_id"]
            if nid in seen:
                continue
            seen.add(nid)
            text = f"{d.get('title', '')} {d.get('description', '')}"
            cands.append({"id": nid, "baslik": d.get("title", ""), "merkez": d.get("center"),
                          "tarih": (d.get("date_created") or "")[:10],
                          "aciklama": re.sub(r"\s+", " ", d.get("description", ""))[:400],
                          "insan_suphesi": bool(PEOPLE.search(text)), "sorgu": query,
                          "href": it.get("href")})
    print(f"{len(cands)} benzersiz video bulundu")
    skip = set(cfg.get("atla", []))
    if cfg.get("onceki"):
        try:
            skip |= {c["id"] for c in json.load(open(cfg["onceki"])) if c.get("tablo")}
        except OSError:
            pass
    if cfg.get("supheliler_dahil"):
        ok = [c for c in cands if not STRONG_PEOPLE.search(c["baslik"] + " " + c["id"])]
    else:
        ok = [c for c in cands if not c["insan_suphesi"]]
    ok = [c for c in ok if c["id"] not in skip]
    svs = svs_candidates(cfg)
    cands += svs
    print(f"SVS: {len(svs)} sonuc")
    ok += [c for c in svs if c["id"] not in skip and (cfg.get("supheliler_dahil") or not c["insan_suphesi"])]
    limit = cfg.get("tablo_limiti", 50)
    for c in ok[:limit]:
        try:
            if c.get("svs"):
                small = svs_prepare(c)
                if not small:
                    continue
                tmp = "/tmp/kesif.mp4"
                req = urllib.request.Request(small, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                    got = 0
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        got += len(chunk)
                        if got > 300 << 20:
                            raise RuntimeError("onizleme dosyasi cok buyuk (>300 MB)")
                info = probe(tmp)
                c["sure"] = info["sure"]
                if info["sure"] < 3:
                    continue
                sheet(tmp, f"{out_dir}/tablolar/{c['id']}.jpg", info["sure"])
                c["tablo"] = f"tablolar/{c['id']}.jpg"
                print(f"  ✓ {c['id']} [{c['tur']}] {info['sure']}s kaynak={c['kaynak']['w']}x"
                      f"{c['kaynak']['h']}  {c['baslik'][:60]}")
                continue
            files = get_json(urllib.parse.quote(c["href"], safe=":/~%"))
            mp4s = [f for f in files if f.endswith(".mp4")]
            c["dosyalar"] = {f.rsplit("~", 1)[-1]: urllib.parse.quote(
                f.replace("http://", "https://"), safe=":/~%") for f in mp4s}
            small = next((c["dosyalar"][k] for k in ("mobile.mp4", "small.mp4", "medium.mp4",
                                                     "large.mp4", "orig.mp4")
                          if k in c["dosyalar"]), None)
            if not small:
                continue
            tmp = "/tmp/kesif.mp4"
            urllib.request.urlretrieve(small, tmp)
            info = probe(tmp)
            best = c["dosyalar"].get("orig.mp4") or c["dosyalar"].get("large.mp4")
            if best:
                try:
                    c["kaynak"] = {"url": best, **probe(best)}
                except subprocess.CalledProcessError:
                    pass
            c["sure"] = info["sure"]
            if info["sure"] < 3:
                continue
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", c["id"])
            sheet(tmp, f"{out_dir}/tablolar/{safe}.jpg", info["sure"])
            c["tablo"] = f"tablolar/{safe}.jpg"
            print(f"  ✓ {c['id']} {info['sure']}s kaynak={c.get('kaynak', {}).get('w')}x"
                  f"{c.get('kaynak', {}).get('h')}  {c['baslik'][:60]}")
        except Exception as e:  # noqa: BLE001  tek klip hatasi kesfi durdurmasin
            print(f"  ✗ {c['id']}: {e}")
    json.dump(cands, open(f"{out_dir}/adaylar.json", "w"), ensure_ascii=False, indent=1)

    music = []
    os.makedirs("/tmp/muzik", exist_ok=True)
    for title in cfg.get("muzik_adaylari", []):
        url = ("https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
               + urllib.parse.quote(title) + ".mp3")
        try:
            path = f"/tmp/muzik/{title}.mp3"
            urllib.request.urlretrieve(url, path)
            music.append({"baslik": title, "url": url, "sure": probe(path)["sure"]})
        except Exception as e:  # noqa: BLE001
            music.append({"baslik": title, "url": url, "hata": str(e)[:100]})
    json.dump(music, open(f"{out_dir}/muzik.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(music, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:3])
