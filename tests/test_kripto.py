import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import kripto_portfoy as kp  # noqa: E402

DAY = 24 * kp.HOUR
NOW = 1791353400000 - (1791353400000 % kp.HOUR) + 10 * 60_000  # bir saatin 10. dakikasi (UTC)


def daily(pattern, n=100, price=100.0):
    out, t0 = [], NOW - (NOW % DAY) - n * DAY
    for i in range(n):
        o = price
        price *= 1 + pattern[i % len(pattern)]
        out.append({"t": t0 + i * DAY, "o": o, "h": max(o, price) * 1.01,
                    "l": min(o, price) * 0.99, "c": price, "T": t0 + (i + 1) * DAY - 1})
    return out


def hourly(prices, start):
    return [{"t": start + i * kp.HOUR, "o": o, "h": max(o, c), "l": min(o, c), "c": c,
             "T": start + (i + 1) * kp.HOUR - 1} for i, (o, c) in enumerate(prices)]


def market(btc_daily, btc_hourly, flat_price=1.0, start=NOW - 3 * kp.HOUR):
    m = {}
    for coin in kp.COINS:
        if coin == "BTC":
            m[coin] = {"daily": btc_daily, "hourly": btc_hourly}
        else:
            m[coin] = {"daily": daily([0.0]), "hourly": hourly([(flat_price, flat_price)], start)}
    return m


UP = [0.02, -0.01]  # yukari trend, RSI ~ 67


class Test(unittest.TestCase):
    def first_run(self):
        st = kp.new_state()
        d = daily(UP)
        price = d[-1]["c"]
        h = hourly([(price, price)] * 2, NOW - 2 * kp.HOUR - 10 * 60_000)
        moves, prices, inds = kp.step(st, market(d, h), NOW)
        return st, d, price, moves, inds

    def test_indicators(self):
        ind = kp.analyze(daily(UP))
        self.assertEqual(ind["signal"], "LONG")
        self.assertTrue(50 <= ind["rsi"] <= 70)
        self.assertEqual(kp.analyze(daily([-0.02, 0.01]))["signal"], "SHORT")
        self.assertEqual(kp.analyze(daily([0.0]))["signal"], "BEKLE")

    def test_open_long_and_rerun_same_day(self):
        st, d, price, moves, _ = self.first_run()
        self.assertEqual([m["islem"] for m in moves], ["LONG açıldı"])
        pos = st["pozisyonlar"]["BTC"]
        self.assertAlmostEqual(pos["teminat"], 20.0)
        self.assertAlmostEqual(pos["miktar"] * price, 60.0)
        self.assertAlmostEqual(pos["likidasyon"] / price, 1 - 1 / 3 + kp.MMR)
        self.assertLess(pos["likidasyon"], pos["stop"])
        self.assertAlmostEqual(st["bakiye"], 200 - 60 * kp.FEE)
        # ayni gun tekrar calisma: yeni islem yok, bugunun hareketi yine gorunur
        h = hourly([(price, price)], st["son_kontrol"])
        moves2, prices, inds = kp.step(st, market(d, h, start=st["son_kontrol"]), NOW + kp.HOUR + 1)
        self.assertEqual(len(moves2), 1)
        self.assertEqual(len(st["pozisyonlar"]), 1)
        subject, md, body = kp.build_report(st, moves2, prices, inds, NOW)
        self.assertIn("hareket VAR", subject)
        self.assertIn("SAHTE PARA", md)

    def test_stop_loss(self):
        st, d, price, _, _ = self.first_run()
        pos = st["pozisyonlar"]["BTC"]
        stop = pos["stop"]
        h = hourly([(price, price * 0.999), (price * 0.999, stop * 0.98)], st["son_kontrol"])
        moves, _, _ = kp.step(st, market(d, h, start=st["son_kontrol"]), NOW + DAY)
        self.assertNotIn("BTC", st["pozisyonlar"])
        trade = st["islemler"][-1]
        self.assertEqual(trade["neden"], "stop")
        self.assertAlmostEqual(trade["cikis"], stop)
        loss = (stop - price) * pos["miktar"] - stop * pos["miktar"] * kp.FEE
        self.assertAlmostEqual(st["bakiye"], 200 - 60 * kp.FEE + loss, places=6)
        self.assertTrue(any("stop-loss" in m["islem"] for m in moves))
        # stop olan coin ayni gun yeniden acilmaz
        self.assertNotIn("BTC", st["pozisyonlar"])

    def test_liquidation_gap(self):
        st, d, price, _, _ = self.first_run()
        liq = st["pozisyonlar"]["BTC"]["likidasyon"]
        h = hourly([(liq * 0.9, liq * 0.9)], st["son_kontrol"])
        kp.step(st, market(d, h, start=st["son_kontrol"]), NOW + DAY)
        self.assertEqual(st["islemler"][-1]["neden"], "likidasyon")
        self.assertAlmostEqual(st["bakiye"], 200 - 60 * kp.FEE - 20.0)

    def test_funding_and_take_profit(self):
        st, d, price, _, _ = self.first_run()
        pos = st["pozisyonlar"]["BTC"]
        start = st["son_kontrol"]
        n = (8 * kp.HOUR - start % (8 * kp.HOUR)) // kp.HOUR + 1  # bir fonlama anini gec
        h = hourly([(price, price)] * n + [(price, pos["hedef"] * 1.01)], start)
        kp.step(st, market(d, h, start=start), NOW + DAY)
        trade = st["islemler"][-1]
        self.assertEqual(trade["neden"], "hedef")
        self.assertAlmostEqual(trade["cikis"], pos["hedef"])
        self.assertLess(trade["net"], (pos["hedef"] - price) * pos["miktar"])  # ucret+fonlama dustu

    def test_max_positions(self):
        st = kp.new_state()
        m = {}
        for i, c in enumerate(kp.COINS):
            d = daily([0.02 - i * 0.0005, -0.01])
            m[c] = {"daily": d, "hourly": hourly([(d[-1]["c"],) * 2], NOW - kp.HOUR - 600_000)}
        moves, prices, inds = kp.step(st, m, NOW)
        longs = [c for c in kp.COINS if inds[c]["signal"] == "LONG"]
        self.assertGreater(len(longs), kp.MAX_POSITIONS)
        self.assertEqual(len(st["pozisyonlar"]), kp.MAX_POSITIONS)
        strongest = sorted(longs, key=lambda c: -inds[c]["guc"])[:kp.MAX_POSITIONS]
        self.assertEqual(set(st["pozisyonlar"]), set(strongest))
        _, md, _ = kp.build_report(st, moves, prices, inds, NOW)
        self.assertIn("sırada", md)


if __name__ == "__main__":
    unittest.main()
