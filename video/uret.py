"""Belgesel uretimi: metin + kurgu -> 1080p video, TR/EN .srt, YouTube metni, kapak.

Kullanim: python3 video/uret.py video/projeler/<proje> <cikti_klasoru>

Proje klasorunde:
  metin.txt   : anlatim (EN), seslendirme (SAY), Turkce altyazi (TR), bolumler, aralar
  kurgu.json  : her bolum icin klip parcalari (kaynak, baslangic, bitis, zoom)
"""
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.request

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
SR = 24000
VOICE, SPEED = "am_michael", 0.95
SENT_GAP = 0.45          # cumleler arasi sessizlik (sn)
INTRO = 6.0              # anlatim baslamadan once baslik + muzik (sn)
OUTRO_MUSIC = 3.0        # son cumleden sonra kurgu devam suresi (sn)
CREDITS = 14.0           # kaynakca ekrani (sn)
XFADE = 0.8              # klipler arasi gecis (sn)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p"]


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        sys.stderr.write(p.stderr[-3000:])
        raise SystemExit(f"Komut basarisiz: {' '.join(cmd[:6])} ...")
    return p


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ metin

def parse_script(path):
    chapters, cur, last = [], None, None
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#") and not line.startswith("##"):
            continue
        if line.startswith("## "):
            en, tr = [x.strip() for x in line[3:].split("|")]
            cur = {"en": en, "tr": tr, "items": []}
            chapters.append(cur)
        elif line.startswith("EN: "):
            last = {"en": line[4:], "say": line[4:], "tr": None}
            cur["items"].append(last)
        elif line.startswith("SAY: "):
            last["say"] = line[5:]
        elif line.startswith("TR: "):
            last["tr"] = line[4:]
        elif m := re.fullmatch(r"\[ara ([\d.]+)\]", line):
            cur["items"].append({"ara": float(m.group(1))})
        else:
            raise SystemExit(f"Anlasilmayan satir: {line}")
    for ch in chapters:
        for it in ch["items"]:
            if "en" in it and not it["tr"]:
                raise SystemExit(f"Turkce ceviri eksik: {it['en']}")
    return chapters


# -------------------------------------------------------------- seslendirme

def synthesize(chapters, cache_dir):
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    os.makedirs(cache_dir, exist_ok=True)
    for ch in chapters:
        for it in ch["items"]:
            if "say" not in it:
                continue
            key = re.sub(r"[^a-z0-9]+", "_", it["say"].lower())[:80]
            path = f"{cache_dir}/{key}_{abs(hash(it['say'])) % 10**8}.wav"
            if not os.path.exists(path):
                audio = np.concatenate([np.asarray(r.audio) for r in
                                        pipe(it["say"], voice=VOICE, speed=SPEED)])
                # bastaki/sondaki sessizligi kirp
                idx = np.where(np.abs(audio) > 0.01)[0]
                if len(idx):
                    audio = audio[max(idx[0] - 600, 0): idx[-1] + 2400]
                sf.write(path, audio, SR)
            it["wav"] = path
            it["dur"] = sf.info(path).duration


def pause_after(sentence):
    """Tekduze okumayi onlemek icin cumle tipine gore degisen duraklama."""
    s = sentence.rstrip()
    if s.endswith("?"):
        return 0.8
    if len(s.split()) <= 6:      # kisa, vurgulu cumleden sonra biraz daha bekle
        return 0.75
    if s.endswith("."):
        return SENT_GAP
    return 0.35


def timeline(chapters):
    """Her cumleye ve bolume baslangic/bitis zamani atar."""
    t = INTRO
    for ch in chapters:
        ch["start"] = t if ch is not chapters[0] else 0.0
        for it in ch["items"]:
            if "ara" in it:
                t += it["ara"]
                continue
            it["start"], it["end"] = t, t + it["dur"]
            t = it["end"] + pause_after(it["en"])
        ch["end"] = t
    chapters[-1]["end"] = t + OUTRO_MUSIC
    return chapters[-1]["end"]


def narration_track(chapters, total, path):
    buf = np.zeros(int((total + CREDITS + 1) * SR), dtype=np.float32)
    for ch in chapters:
        for it in ch["items"]:
            if "wav" in it:
                a, _ = sf.read(it["wav"], dtype="float32")
                i = int(it["start"] * SR)
                buf[i:i + len(a)] += a
    sf.write(path, buf, SR)


# ---------------------------------------------------------------- altyazi

def split_lines(text, width=42):
    lines = textwrap.wrap(text, width)
    if len(lines) <= 2:
        if len(lines) == 2:  # satirlari dengele
            words = text.split()
            best = min(range(1, len(words)), key=lambda i: abs(
                len(" ".join(words[:i])) - len(" ".join(words[i:]))))
            a, b = " ".join(words[:best]), " ".join(words[best:])
            if len(a) <= width and len(b) <= width:
                return [a, b]
        return lines
    return None


def chunk_text(text, max_chars=84):
    """Metni en fazla 2 satir x 42 karakterlik parcalara boler (noktalama oncelikli)."""
    if split_lines(text):
        return [text]
    words = text.split()
    best, best_score = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_chars:
            break
        if min(len(a), len(b)) < 22:   # cok kisa parca olmasin
            continue
        score = abs(len(a) - len(b)) - (15 if re.search(r"[,;:—–-]$", words[i - 1]) else 0)
        if best_score is None or score < best_score:
            best, best_score = i, score
    if best is None:
        best = len(words) // 2
    return chunk_text(" ".join(words[:best]), max_chars) + chunk_text(" ".join(words[best:]), max_chars)


def fmt_ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(chapters, lang):
    cues = []
    for ch in chapters:
        for it in ch["items"]:
            if "en" not in it:
                continue
            parts = chunk_text(it[lang])
            total_chars = sum(len(p) for p in parts)
            t = it["start"]
            dur = it["end"] - it["start"]
            for p in parts:
                d = dur * len(p) / total_chars
                cues.append([t, t + d, p])
                t += d
    # okuma suresi: cumle sonrasi sessizlige 0,6 sn'ye kadar tasabilir; en az 1,2 sn;
    # bir sonraki altyaziyla cakismaz
    for i, c in enumerate(cues):
        nxt = cues[i + 1][0] if i + 1 < len(cues) else c[1] + 2
        c[1] = min(max(c[1] + 0.6, c[0] + 1.2), nxt - 0.08)
        c[1] = max(c[1], c[0] + 0.5)
    out = []
    for i, (a, b, text) in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n" + "\n".join(split_lines(text)) + "\n")
    return "\n".join(out), cues


# ------------------------------------------------------------------ gorsel

def font(path, size):
    return ImageFont.truetype(path, size)


def spaced(draw, xy, text, fnt, fill, spacing, anchor_center=True):
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + spacing
    return total


def title_png(title, subtitle, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for layer, fill in ((glow, (0, 0, 0, 200)), (img, (255, 255, 255, 255))):
        d = ImageDraw.Draw(layer)
        spaced(d, (W / 2, H / 2 - 95), title, font(FONT_B, 120), fill, 22)
        spaced(d, (W / 2, H / 2 + 60), subtitle.upper(), font(FONT, 38), fill, 9)
    glow = glow.filter(ImageFilter.GaussianBlur(14))
    Image.alpha_composite(glow, img).save(path)


def chapter_png(en, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for layer, fill, line in ((glow, (0, 0, 0, 220), None), (img, (255, 255, 255, 240), 1)):
        d = ImageDraw.Draw(layer)
        width = spaced(d, (120, 860), en.upper(), font(FONT_B, 44), fill, 8, anchor_center=False)
        if line:
            d.rectangle([120, 838, 120 + min(width, 160), 842], fill=(255, 190, 90, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    Image.alpha_composite(glow, img).save(path)


def credits_png(lines, path, y0=None):
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 0 if y0 is None else y0
    for kind, text in lines:
        if kind == "h":
            y += 26
            spaced(d, (W / 2, y), text.upper(), font(FONT_B, 34), (255, 196, 120), 6)
            y += 58
        else:
            for ln in textwrap.wrap(text, 80):
                w = d.textlength(ln, font=font(FONT, 30))
                d.text(((W - w) / 2, y), ln, font=font(FONT, 30), fill=(225, 225, 225))
                y += 42
    if y0 is None:  # once yuksekligi olc, sonra dikeyde ortala
        return credits_png(lines, path, max((H - y) // 2, 40))
    img.save(path)


def thumbnail(frame, title, path):
    img = Image.open(frame).convert("RGB").resize((1280, 720))
    shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle([0, 470, 1280, 720], fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img.convert("RGBA"), shade.filter(ImageFilter.GaussianBlur(30)))
    d = ImageDraw.Draw(img)
    size = 118
    while d.textlength(title, font=font(FONT_B, size)) > 1180:
        size -= 4
    w = d.textlength(title, font=font(FONT_B, size))
    for dx, dy in ((4, 4), (0, 0)):
        d.text(((1280 - w) / 2 + dx, 540 + dy), title, font=font(FONT_B, size),
               fill=(0, 0, 0) if dx else (255, 214, 92))
    img.convert("RGB").save(path, quality=92)


# ------------------------------------------------------------------- kurgu

def fetch_sources(edit, src_dir):
    os.makedirs(src_dir, exist_ok=True)
    paths = {}
    for key, src in edit["kaynaklar"].items():
        path = f"{src_dir}/{key}.mp4"
        if not os.path.exists(path):
            log(f"  indiriliyor: {key}")
            urllib.request.urlretrieve(src["url"], path)
        paths[key] = path
    return paths


def render_segment(src, start, length, dur, zoom, out):
    k = dur / length  # >1 yavaslatma
    vf = (f"setpts={k:.5f}*(PTS-STARTPTS),"
          f"scale={int(W * zoom)}:{int(H * zoom)}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},fps={FPS},format=yuv420p")
    run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", src, "-vf", vf, "-t", f"{dur:.3f}", "-an", *X264, out])


def render_chapter(idx, segs, D, paths, work, overlay=None, label=None):
    """Bolumun klip parcalarini D saniyeye yayar, aralarina gecis koyar."""
    n = len(segs)
    lengths = [s[2] - s[1] for s in segs]
    k = (D + (n - 1) * XFADE) / sum(lengths)
    log(f"  bolum {idx}: {n} parca, {D:.1f} sn, hiz carpani {1 / k:.2f}x")
    if not 0.5 <= k <= 2.2:
        raise SystemExit(f"Bolum {idx}: klip suresi uygun degil (k={k:.2f}). Kurguyu duzeltin.")
    files = []
    for j, (key, a, b, *opt) in enumerate(segs):
        o = opt[0] if opt else {}
        out = f"{work}/b{idx:02d}_{j:02d}.mp4"
        render_segment(paths[key], a, b - a, (b - a) * k, o.get("zoom", 1.0), out)
        files.append(out)
    inputs, chain, prev, off = [], [], "0:v", 0.0
    for j, f in enumerate(files):
        inputs += ["-i", f]
    for j in range(1, n):
        off += lengths[j - 1] * k - XFADE
        lbl = f"x{j}"
        chain.append(f"[{prev}][{j}:v]xfade=transition=fade:duration={XFADE}:offset={off:.3f}[{lbl}]")
        prev = lbl
    fades = f"fade=t=in:st=0:d=0.6,fade=t=out:st={D - 0.6:.3f}:d=0.6"
    chain.append(f"[{prev}]{fades}[base]")
    cur = "base"
    for m, (png, st, en_) in enumerate(x for x in ((overlay, 0.8, INTRO - 1.4),
                                                    (label, 0.9, 5.4)) if x[0]):
        inputs += ["-loop", "1", "-t", f"{D:.3f}", "-i", png]
        chain.append(f"[{n + m}:v]format=rgba,fade=t=in:st={st}:d=1.0:alpha=1,"
                     f"fade=t=out:st={en_}:d=1.0:alpha=1[ov{m}]")
        chain.append(f"[{cur}][ov{m}]overlay=0:0:shortest=1[o{m}]")
        cur = f"o{m}"
    chain.append(f"[{cur}]null[v]")
    out = f"{work}/bolum_{idx:02d}.mp4"
    run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(chain),
         "-map", "[v]", "-t", f"{D:.3f}", "-r", str(FPS), *X264, out])
    for f in files:
        os.remove(f)
    return out


# -------------------------------------------------------------------- ses

def music_bed(tracks, total, work):
    files = []
    for t in tracks:
        path = f"{work}/{t}.mp3"
        if not os.path.exists(path):
            url = ("https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
                   + urllib.parse.quote(t) + ".mp3")
            urllib.request.urlretrieve(url, path)
        files.append(path)
    inputs = sum((["-i", f] for f in files), [])
    chain, prev = [], "0:a"
    for j in range(1, len(files)):
        chain.append(f"[{prev}][{j}:a]acrossfade=d=6:c1=tri:c2=tri[m{j}]")
        prev = f"m{j}"
    chain.append(f"[{prev}]atrim=0:{total:.3f},afade=t=in:d=3,"
                 f"afade=t=out:st={total - 8:.3f}:d=8,loudnorm=I=-24:TP=-3[out]")
    out = f"{work}/muzik.wav"
    run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(chain),
         "-map", "[out]", "-ar", "48000", "-ac", "2", out])
    return out


def mix_audio(narr, music, total, out):
    graph = ("[0:a]aresample=48000,loudnorm=I=-16:TP=-2,pan=stereo|c0=c0|c1=c0,"
             "asplit=2[voice][sc];"
             "[1:a][sc]sidechaincompress=threshold=0.02:ratio=6:attack=80:release=900[duck];"
             "[voice][duck]amix=inputs=2:normalize=0:duration=longest,"
             f"atrim=0:{total:.3f}[mix]")
    tmp = out + ".tmp.wav"
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", narr, "-i", music, "-filter_complex", graph,
         "-map", "[mix]", "-ar", "48000", tmp])
    # iki gecisli loudness: YouTube -14 LUFS
    p = run(["ffmpeg", "-hide_banner", "-i", tmp, "-af",
             "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    m = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
    af = ("loudnorm=I=-14:TP=-1.5:LRA=11:"
          f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true")
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp, "-af", af, "-ar", "48000", out])
    os.remove(tmp)


# ------------------------------------------------------------------- main

def youtube_text(cfg, chapters, total):
    def ts(t):
        t = int(t)
        return f"{t // 60:02d}:{t % 60:02d}"
    tr, en = cfg["youtube"]["tr"], cfg["youtube"]["en"]
    chap_tr = "\n".join(f"{ts(c['start'])} {c['tr']}" for c in chapters)
    chap_en = "\n".join(f"{ts(c['start'])} {c['en']}" for c in chapters)
    credits = "\n".join(cfg["kaynakca"])
    refs = "\n".join(cfg.get("bilgi_kaynaklari", []))
    return f"""=== BASLIK (Turkce) ===
{tr['baslik']}

=== BASLIK (English) ===
{en['baslik']}

=== ACIKLAMA (Turkce kanal icin) ===
{tr['aciklama']}

Bölümler:
{chap_tr}

Türkçe altyazı mevcuttur (CC).

Kaynaklar ve lisanslar:
{credits}

Bilgi kaynakları:
{refs}

=== DESCRIPTION (English channel) ===
{en['aciklama']}

Chapters:
{chap_en}

Credits & licenses:
{credits}

Sources:
{refs}

=== ETIKETLER / TAGS ===
{", ".join(cfg['youtube']['etiketler'])}

=== YOUTUBE AYARLARI ===
- Video dili: English
- Altyazi: altyazi_tr.srt -> Turkce, altyazi_en.srt -> English
- "Altered or synthetic content" sorusu: YouTube bunu gercek kisi/olay sanilabilecek sahte
  goruntu ve sesler icin istiyor. Bu videoda gorseller gercek NASA kayitlari, anlatici kimseyi
  taklit etmiyor; bu yuzden "Hayir" uygundur. Bu etiket para kazanmayi etkilemez.
- Para kazanma icin: videolari aralikli yukleyin (or. haftada 1-2), her videoda kendi basliginizi
  ve aciklamanizi kisisellestirin, yorumlara cevap verin.
- Sure: {ts(total + CREDITS)}
"""


def check_segments(cfg, paths, out_dir):
    """Kurgudaki her parcanin bas/orta/son karesini gercek kaynaktan cikarir."""
    durs = {}
    for key, path in paths.items():
        durs[key] = float(json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                                          "format=duration", "-of", "json", path]).stdout)
                          ["format"]["duration"])
    for i, segs in enumerate(cfg["bolumler"]):
        rows = []
        for j, (key, a, b, *_) in enumerate(segs):
            row = Image.new("RGB", (3 * 320 + 260, 180), (0, 0, 0))
            d = ImageDraw.Draw(row)
            d.text((970, 20), f"{i}.{j} {key}", font=font(FONT_B, 22), fill=(255, 220, 0))
            d.text((970, 60), f"{a:.1f}-{b:.1f}s", font=font(FONT, 20), fill=(255, 255, 255))
            warn = "KAYNAK KISA!" if b > durs[key] else f"kaynak {durs[key]:.0f}s"
            d.text((970, 95), warn, font=font(FONT, 20),
                   fill=(255, 80, 80) if "KISA" in warn else (180, 180, 180))
            for m, t in enumerate((a + 0.3, (a + b) / 2, b - 0.3)):
                tmp = f"/tmp/kare_{m}.png"
                run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{min(t, durs[key] - 0.1):.2f}",
                     "-i", paths[key], "-frames:v", "1", "-vf", "scale=320:180", tmp])
                row.paste(Image.open(tmp).convert("RGB"), (m * 320, 0))
            rows.append(row)
        sheet = Image.new("RGB", (rows[0].width, 180 * len(rows)))
        for j, r in enumerate(rows):
            sheet.paste(r, (0, 180 * j))
        sheet.save(f"{out_dir}/parca_kontrol_{i:02d}.jpg", quality=80)
    log("Parca kontrol tablolari hazir.")


def main(project, out_dir):
    cfg = json.load(open(f"{project}/kurgu.json"))
    work = "/tmp/uretim"
    os.makedirs(work, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    if os.environ.get("SADECE_KONTROL"):
        check_segments(cfg, fetch_sources(cfg, f"{work}/kaynak"), out_dir)
        return
    chapters = parse_script(f"{project}/metin.txt")
    if len(cfg["bolumler"]) != len(chapters):
        raise SystemExit(f"kurgu.json {len(cfg['bolumler'])} bolum, metin {len(chapters)} bolum")

    log("1/6 Seslendirme")
    synthesize(chapters, f"{work}/tts")
    total = timeline(chapters)
    log(f"   anlatim + aralar: {total / 60:.2f} dk; kaynakca ile {(total + CREDITS) / 60:.2f} dk")
    narration_track(chapters, total, f"{work}/anlatim.wav")

    log("2/6 Altyazilar")
    srt_tr, cues_tr = build_srt(chapters, "tr")
    srt_en, _ = build_srt(chapters, "en")
    open(f"{out_dir}/altyazi_tr.srt", "w", encoding="utf-8").write(srt_tr)
    open(f"{out_dir}/altyazi_en.srt", "w", encoding="utf-8").write(srt_en)
    longest = max(max(len(x) for x in split_lines(c[2])) for c in cues_tr)
    log(f"   {len(cues_tr)} Turkce altyazi; en uzun satir {longest} karakter")

    log("3/6 Klipler")
    paths = fetch_sources(cfg, f"{work}/kaynak")
    title_png(cfg["baslik"], cfg["alt_baslik"], f"{work}/baslik.png")
    parts = []
    for i, (ch, segs) in enumerate(zip(chapters, cfg["bolumler"])):
        D = ch["end"] - ch["start"]
        label = None
        if i > 0:
            label = f"{work}/bolum_{i:02d}.png"
            chapter_png(ch["en"], label)
        parts.append(render_chapter(i, segs, D, paths, work,
                                    overlay=f"{work}/baslik.png" if i == 0 else None,
                                    label=label))
    credits_png([tuple(x) for x in cfg["kaynakca_ekran"]], f"{work}/kaynakca.png")
    run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-t", str(CREDITS), "-i",
         f"{work}/kaynakca.png", "-vf", f"fps={FPS},format=yuv420p,fade=t=in:d=1,"
         f"fade=t=out:st={CREDITS - 1.5}:d=1.5", *X264, f"{work}/kaynakca.mp4"])
    parts.append(f"{work}/kaynakca.mp4")
    with open(f"{work}/liste.txt", "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i",
         f"{work}/liste.txt", "-c", "copy", f"{work}/goruntu.mp4"])

    log("4/6 Muzik ve ses karisimi")
    music = music_bed(cfg["muzik"], total + CREDITS, work)
    mix_audio(f"{work}/anlatim.wav", music, total + CREDITS, f"{work}/ses.wav")

    log("5/6 Birlestirme")
    final = f"{out_dir}/{cfg['dosya_adi']}.mp4"
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", f"{work}/goruntu.mp4", "-i",
         f"{work}/ses.wav", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
         "-b:a", "320k", "-ar", "48000", "-shortest", "-movflags", "+faststart", final])

    log("6/6 YouTube paketi")
    open(f"{out_dir}/youtube_aciklama.txt", "w", encoding="utf-8").write(
        youtube_text(cfg, chapters, total))
    run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(cfg.get("kapak_an", 20)), "-i", final,
         "-frames:v", "1", f"{work}/kapak_kare.png"])
    thumbnail(f"{work}/kapak_kare.png", cfg["kapak_yazisi"], f"{out_dir}/kapak.jpg")
    info = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration,size,bit_rate", "-of", "json", final]).stdout)
    log(f"   video: {float(info['format']['duration']) / 60:.2f} dk, "
        f"{int(info['format']['size']) / 1e6:.0f} MB")


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (music_bed)
    main(*sys.argv[1:3])
