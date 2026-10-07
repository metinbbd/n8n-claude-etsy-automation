"""Kripto SANAL portfoy (SAHTE PARA).

- Borsa hesabi YOK, API anahtari YOK, gercek emir YOK.
- Fiyatlar: Binance halka acik piyasa verisi (data-api.binance.vision).
  GitHub sunuculari ABD'de oldugu icin Binance vadeli API'si (fapi) engelli;
  3x vadeli islemler spot fiyatlarla simule edilir. Fonlama ucreti standart
  oranla (8 saatte %0,01) tahmini hesaplanir.
- Baslangic: 200 USDT, 10 coin, 3x kaldirac, izole teminat, long/short.

Kurallar (gunluk mumlara gore, her sabah):
  LONG  : EMA20 > EMA50, fiyat > EMA20 ve RSI 50-70
  SHORT : EMA20 < EMA50, fiyat < EMA20 ve RSI 30-50
  Teminat: toplam degerin %10'u (coin basina en fazla 1 pozisyon)
  Stop  : 2 x ATR (en az %3, en fazla %15) · Hedef: stop mesafesinin 2 kati
  Trend bozulursa (fiyat EMA20'nin ters tarafina gecerse) pozisyon kapanir.
  Gun icinde stop/hedef/likidasyon saatlik mumlarla kontrol edilir.
"""
import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT"]
START_BALANCE = 200.0
LEVERAGE = 3
FEE = 0.0005          # Binance vadeli taker ucreti (islem basina)
MMR = 0.005           # bakim teminati orani (likidasyon hesabi)
FUNDING = 0.0001      # 8 saatlik tahmini fonlama: long oder, short alir
MARGIN_PCT = 0.10
MIN_MARGIN = 5.0
HOUR = 3600_000
IST = timezone(timedelta(hours=3))
DATA_API = "https://data-api.binance.vision/api/v3/klines"

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STATE_FILE = os.path.join(ROOT, "data", "kripto", "portfoy.json")
REPORT_DIR = os.path.join(ROOT, "raporlar", "kripto")

NEDEN = {"hedef": "hedef fiyata ulaştı", "stop": "stop-loss", "trend": "trend bozuldu",
         "likidasyon": "LİKİDASYON"}


# ------------------------------------------------------------------ veri

def fetch_klines(symbol, interval, start_ms=None, limit=1000):
    url = f"{DATA_API}?symbol={symbol}&interval={interval}&limit={limit}"
    if start_ms is not None:
        url += f"&startTime={int(start_ms)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        rows = json.loads(r.read())
    return [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
             "c": float(k[4]), "T": k[6]} for k in rows]


def fetch_market(now_ms, since_ms):
    market = {}
    for coin in COINS:
        sym = f"{coin}USDT"
        daily = [k for k in fetch_klines(sym, "1d", limit=200) if k["T"] < now_ms]
        hourly, start = [], since_ms
        while True:
            chunk = [k for k in fetch_klines(sym, "1h", start) if k["T"] < now_ms]
            hourly += chunk
            if len(chunk) < 999:
                break
            start = chunk[-1]["t"] + HOUR
        market[coin] = {"daily": daily, "hourly": hourly}
    return market


# ----------------------------------------------------------- gostergeler

def ema(values, n):
    k = 2 / (n + 1)
    out = sum(values[:n]) / n
    for v in values[n:]:
        out = v * k + out * (1 - k)
    return out


def rsi(closes, n=14):
    gains = [max(b - a, 0) for a, b in zip(closes, closes[1:])]
    losses = [max(a - b, 0) for a, b in zip(closes, closes[1:])]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, lo in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + lo) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def atr(candles, n=14):
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"]))
           for p, c in zip(candles, candles[1:])]
    out = sum(trs[:n]) / n
    for tr in trs[n:]:
        out = (out * (n - 1) + tr) / n
    return out


def analyze(daily):
    closes = [c["c"] for c in daily]
    if len(closes) < 60:
        return None
    e20, e50, r = ema(closes, 20), ema(closes, 50), rsi(closes)
    close = closes[-1]
    if e20 > e50 and close > e20 and 50 <= r <= 70:
        signal = "LONG"
    elif e20 < e50 and close < e20 and 30 <= r <= 50:
        signal = "SHORT"
    else:
        signal = "BEKLE"
    return {"close": close, "prev": closes[-2], "ema20": e20, "ema50": e50, "rsi": r,
            "atr": atr(daily), "signal": signal,
            "trend": "yukarı" if e20 > e50 else "aşağı"}


# ----------------------------------------------------------------- motor

def new_state():
    return {"baslangic_bakiye": START_BALANCE, "bakiye": START_BALANCE, "pozisyonlar": {},
            "hareketler": [], "islemler": [], "gecmis": [], "son_kontrol": None}


def ist_day(ms):
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%Y-%m-%d")


def direction(pos):
    return 1 if pos["yon"] == "LONG" else -1


def unrealized(pos, price):
    return (price - pos["giris"]) * pos["miktar"] * direction(pos)


def equity(state, prices):
    return state["bakiye"] + sum(unrealized(p, prices[c]) for c, p in state["pozisyonlar"].items())


def open_position(state, coin, side, price, ind, ts, prices, day):
    margin = round(equity(state, prices) * MARGIN_PCT, 2)
    used = sum(p["teminat"] for p in state["pozisyonlar"].values())
    if margin < MIN_MARGIN or margin > state["bakiye"] - used:
        return False
    notional = margin * LEVERAGE
    dist = min(max(2 * ind["atr"] / price, 0.03), 0.15)
    sgn = 1 if side == "LONG" else -1
    pos = {
        "yon": side, "giris": price, "miktar": notional / price, "teminat": margin,
        "stop": price * (1 - sgn * dist), "hedef": price * (1 + sgn * 2 * dist),
        "likidasyon": price * (1 - sgn * (1 / LEVERAGE - MMR)),
        "acilis": ts, "ucret": notional * FEE, "fonlama": 0.0,
    }
    state["bakiye"] -= pos["ucret"]
    state["pozisyonlar"][coin] = pos
    state["hareketler"].append({"gun": day, "ts": ts, "coin": coin,
                                "islem": f"{side} açıldı", "fiyat": price, "teminat": margin})
    return True


def close_position(state, coin, price, reason, ts, day):
    pos = state["pozisyonlar"].pop(coin)
    if reason == "likidasyon":
        pnl, fee = -pos["teminat"], 0.0
    else:
        pnl, fee = unrealized(pos, price), price * pos["miktar"] * FEE
    state["bakiye"] += pnl - fee
    net = pnl - fee - pos["ucret"] + pos["fonlama"]
    state["islemler"].append({"coin": coin, "yon": pos["yon"], "giris": pos["giris"],
                              "cikis": price, "acilis": pos["acilis"], "kapanis": ts,
                              "neden": reason, "net": net, "teminat": pos["teminat"]})
    # Gece kapanan pozisyon da o sabahin raporunda gorunsun diye gun = calisma gunu.
    state["hareketler"].append({"gun": day, "ts": ts, "coin": coin,
                                "islem": f"{pos['yon']} kapandı ({NEDEN[reason]})",
                                "fiyat": price, "net": net})


def walk_candle(state, coin, c, day):
    """Bir saatlik mumda fonlama + likidasyon/stop/hedef kontrolu."""
    pos = state["pozisyonlar"][coin]
    if c["t"] % (8 * HOUR) == 0 and c["t"] > pos["acilis"]:
        f = -direction(pos) * c["o"] * pos["miktar"] * FUNDING
        pos["fonlama"] += f
        state["bakiye"] += f
    d, ts = direction(pos), c["T"]
    worst, best = (c["l"], c["h"]) if d == 1 else (c["h"], c["l"])
    beyond = lambda price, level: (price - level) * d <= 0  # noqa: E731  zarar yonunde gecti mi
    if beyond(c["o"], pos["likidasyon"]):
        close_position(state, coin, pos["likidasyon"], "likidasyon", ts, day)
    elif beyond(c["o"], pos["stop"]):
        close_position(state, coin, c["o"], "stop", ts, day)
    elif beyond(worst, pos["stop"]):          # ayni mumda ikisi de varsa once stop (temkinli)
        close_position(state, coin, pos["stop"], "stop", ts, day)
    elif (c["o"] - pos["hedef"]) * d >= 0:
        close_position(state, coin, c["o"], "hedef", ts, day)
    elif (best - pos["hedef"]) * d >= 0:
        close_position(state, coin, pos["hedef"], "hedef", ts, day)


def step(state, market, now_ms):
    """Bir sabah calismasi. Bugunun hareket listesini dondurur."""
    last = state["son_kontrol"]
    today = ist_day(now_ms)
    for coin in list(state["pozisyonlar"]):
        for c in market[coin]["hourly"]:
            if coin not in state["pozisyonlar"]:
                break
            if last is not None and c["t"] >= last:
                walk_candle(state, coin, c, today)

    prices, latest = {}, 0
    for coin in COINS:
        h = market[coin]["hourly"]
        prices[coin] = h[-1]["c"] if h else market[coin]["daily"][-1]["c"]
        latest = max(latest, h[-1]["T"] + 1 if h else 0)
    ts = latest or now_ms
    inds = {coin: analyze(market[coin]["daily"]) for coin in COINS}

    for coin, pos in list(state["pozisyonlar"].items()):
        ind = inds[coin]
        if not ind:
            continue
        broken = (ind["close"] - ind["ema20"]) * direction(pos) < 0
        flipped = ind["signal"] not in ("BEKLE", pos["yon"])
        if broken or flipped:
            close_position(state, coin, prices[coin], "trend", ts, today)

    closed_today = {h["coin"] for h in state["hareketler"]
                    if h["gun"] == today and "kapandı" in h["islem"]}
    for coin in COINS:
        ind = inds[coin]
        if (ind and ind["signal"] != "BEKLE" and coin not in state["pozisyonlar"]
                and coin not in closed_today):
            open_position(state, coin, ind["signal"], prices[coin], ind, ts, prices, today)

    state["son_kontrol"] = ts
    value = equity(state, prices)
    state["gecmis"] = [g for g in state["gecmis"] if g["gun"] != today] + [
        {"gun": today, "deger": round(value, 4)}]
    return [h for h in state["hareketler"] if h["gun"] == today], prices, inds


# ----------------------------------------------------------------- rapor

def num(x, d=2):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fiyat(x):
    return num(x, 2 if x >= 100 else 4 if x >= 1 else 5)


def sgn(x, d=2):
    return ("+" if x > 0 else "") + num(x, d)


def build_report(state, moves, prices, inds, now_ms):
    day = datetime.fromtimestamp(now_ms / 1000, IST)
    value = equity(state, prices)
    start = state["baslangic_bakiye"]
    durum = "VAR" if moves else "YOK"
    subject = (f"Kripto sanal portföy – hareket {durum}"
               + (f" ({len(moves)} işlem)" if moves else "")
               + f" · {num(value)} USDT ({sgn((value / start - 1) * 100, 1)}%)")

    blocks = [("p", "⚠️ SAHTE PARA ile simülasyon. Borsa hesabı yok, gerçek emir verilmedi. "
                    "Fiyatlar Binance halka açık verisi.")]
    blocks.append(("h", f"Bugün hareket {durum}"))
    if moves:
        blocks.append(("table", ["Coin", "İşlem", "Fiyat", "Teminat / Sonuç"],
                       [[m["coin"], m["islem"], fiyat(m["fiyat"]),
                         f"{num(m['teminat'])} USDT teminat" if "teminat" in m
                         else f"{sgn(m['net'])} USDT"] for m in moves]))
    else:
        blocks.append(("p", "Yeni pozisyon açılmadı, kapanan pozisyon yok."))

    used = sum(p["teminat"] for p in state["pozisyonlar"].values())
    upnl = value - state["bakiye"]
    week = [g for g in state["gecmis"] if g["gun"] <= (day - timedelta(days=7)).strftime("%Y-%m-%d")]
    rows = [["Başlangıç", f"{num(start)} USDT"],
            ["Toplam değer", f"{num(value)} USDT ({sgn((value / start - 1) * 100, 1)}%)"],
            ["Cüzdan bakiyesi", f"{num(state['bakiye'])} USDT"],
            ["Açık pozisyon kâr/zarar", f"{sgn(upnl)} USDT"],
            ["Kullanılan teminat", f"{num(used)} USDT"]]
    if week:
        rows.append(["Son 7 gün", f"{sgn(value - week[-1]['deger'])} USDT"])
    blocks += [("h", "Portföy"), ("table", ["", ""], rows)]

    if state["pozisyonlar"]:
        prow = []
        for coin, p in sorted(state["pozisyonlar"].items()):
            u = unrealized(p, prices[coin])
            prow.append([coin, f"{p['yon']} {LEVERAGE}x", fiyat(p["giris"]), fiyat(prices[coin]),
                         fiyat(p["stop"]), fiyat(p["hedef"]), fiyat(p["likidasyon"]),
                         f"{sgn(u)} USDT ({sgn(u / p['teminat'] * 100, 1)}%)"])
        blocks += [("h", "Açık pozisyonlar"),
                   ("table", ["Coin", "Yön", "Giriş", "Şimdi", "Stop", "Hedef", "Likidasyon",
                              "Kâr/Zarar"], prow)]
    else:
        blocks += [("h", "Açık pozisyonlar"), ("p", "Açık pozisyon yok.")]

    srow = []
    for coin in COINS:
        ind = inds.get(coin)
        if not ind:
            srow.append([coin, fiyat(prices[coin]), "—", "—", "—", "veri yetersiz"])
            continue
        srow.append([coin, fiyat(prices[coin]), sgn((ind["close"] / ind["prev"] - 1) * 100, 1) + "%",
                     ind["trend"], num(ind["rsi"], 0), ind["signal"]])
    blocks += [("h", "Günlük sinyaller"),
               ("table", ["Coin", "Fiyat", "Dünkü değişim", "Trend", "RSI", "Sinyal"], srow)]

    trades = state["islemler"]
    if trades:
        wins = sum(1 for t in trades if t["net"] > 0)
        blocks += [("h", "Kapanan işlemler (tümü)"),
                   ("p", f"{len(trades)} işlem · kazanan %{num(wins / len(trades) * 100, 0)} · "
                         f"toplam {sgn(sum(t['net'] for t in trades))} USDT")]
    blocks.append(("small", "Kurallar: EMA20/EMA50 trendi + RSI ile LONG/SHORT; teminat toplam "
                            "değerin %10'u, 3x kaldıraç; stop 2×ATR (%3–15), hedef 2×stop; trend "
                            "bozulunca kapanır. Ücret %0,05; fonlama 8 saatte %0,01 (tahmini). "
                            "Bu bir yatırım tavsiyesi değildir."))
    title = f"Kripto sanal portföy – {day.strftime('%d.%m.%Y')}"
    return subject, to_markdown(title, blocks), to_html(title, blocks)


def to_markdown(title, blocks):
    out = [f"# {title}", ""]
    for b in blocks:
        if b[0] == "h":
            out += [f"## {b[1]}", ""]
        elif b[0] in ("p", "small"):
            out += [b[1], ""]
        else:
            heads = b[1] if any(b[1]) else [" "] * len(b[1])
            out += ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
            out += ["| " + " | ".join(str(c) for c in r) + " |" for r in b[2]] + [""]
    return "\n".join(out)


def to_html(title, blocks):
    e = html.escape
    out = ["<div style='font-family:Arial,sans-serif;max-width:720px;color:#222'>",
           f"<h2 style='color:#1f6feb'>{e(title)}</h2>"]
    for b in blocks:
        if b[0] == "h":
            color = "#1a7f37" if b[1].endswith("VAR") else "#222"
            out.append(f"<h3 style='color:{color}'>{e(b[1])}</h3>")
        elif b[0] == "p":
            out.append(f"<p>{e(b[1])}</p>")
        elif b[0] == "small":
            out.append(f"<p style='color:#888;font-size:12px'>{e(b[1])}</p>")
        else:
            out.append("<table style='border-collapse:collapse;width:100%;font-size:13px'>")
            if any(b[1]):
                out.append("<tr>" + "".join(
                    f"<th style='text-align:left;padding:5px 8px;background:#eef3fb'>{e(h)}</th>"
                    for h in b[1]) + "</tr>")
            for r in b[2]:
                out.append("<tr>" + "".join(
                    f"<td style='padding:5px 8px;border-bottom:1px solid #eee'>{e(str(c))}</td>"
                    for c in r) + "</tr>")
            out.append("</table>")
    out.append("</div>")
    return "\n".join(out)


# ------------------------------------------------------------------ main

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return new_state()


def save(state, md, now_ms):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    path = os.path.join(REPORT_DIR, f"{ist_day(now_ms)}.md")
    with open(path, "w") as f:
        f.write(md)
    with open(os.path.join(ROOT, "raporlar", "kripto", "SON_RAPOR.md"), "w") as f:
        f.write(md)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-mail", action="store_true")
    args = ap.parse_args()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    state = load_state()
    since = state["son_kontrol"] or now_ms - 3 * HOUR
    since = max(since, now_ms - 40 * 24 * HOUR)
    try:
        market = fetch_market(now_ms, since)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"HATA: Binance verisi alinamadi: {e}")
        sys.exit(1)
    moves, prices, inds = step(state, market, now_ms)
    subject, md, body = build_report(state, moves, prices, inds, now_ms)
    path = save(state, md, now_ms)
    print(f"{subject}\nRapor: {os.path.relpath(path, ROOT)}")

    if args.no_mail:
        print("Test modu: mail gonderilmedi.")
        return
    from n8n_common import RaporHatasi, send_mail
    try:
        send_mail(subject, body)
        print("Rapor maili gonderildi.")
    except RaporHatasi as e:
        # Portfoy yine de kaydedilir; rapor depoda da durur.
        print(f"::warning::Mail gonderilemedi: {e}")


if __name__ == "__main__":
    main()
