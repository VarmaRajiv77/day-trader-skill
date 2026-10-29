import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import ROOT, load

india = load("trader-india", "india")

DAY = datetime.date(2026, 10, 29)  # a Thursday, not an NSE holiday


def ts(day, hh, mm):
    """India time -> unix seconds (IST is UTC+5:30, no daylight saving)."""
    utc = datetime.datetime.combine(day, datetime.time(hh, mm)) - datetime.timedelta(hours=5, minutes=30)
    return int((utc - datetime.datetime(1970, 1, 1)).total_seconds())


def chart(sym, bars, day=DAY):
    """Synthetic v8 chart: bars = [(o, h, l, c, v)] from 9:15 IST, one every 5 minutes.
    Made-up numbers for the tests, not market data."""
    t = [ts(day, 9, 15) + 300 * k for k in range(len(bars))]
    q = {"open": [b[0] for b in bars], "high": [b[1] for b in bars], "low": [b[2] for b in bars],
         "close": [b[3] for b in bars], "volume": [b[4] for b in bars]}
    return {"chart": {"result": [{"meta": {"symbol": sym + ".NS", "currency": "INR"}, "timestamp": t,
                                  "indicators": {"quote": [q]}}], "error": None}}


# opening range 9:15 to 9:30: high 100.50, low 99.00. The 9:35 bar closes 101.00, over the high.
ORB_BARS = [(99.5, 100.2, 99.0, 100.0, 1000), (100.0, 100.5, 99.6, 100.3, 900), (100.3, 100.4, 99.8, 100.1, 800),
            (100.1, 100.4, 99.9, 100.2, 700), (100.2, 101.2, 100.1, 101.0, 1500), (101.0, 101.3, 100.7, 101.1, 900)]
FLAT_BARS = [(99.5, 100.2, 99.0, 100.0, 1000), (100.0, 100.5, 99.6, 100.3, 900), (100.3, 100.4, 99.8, 100.1, 800),
             (100.1, 100.4, 99.9, 100.2, 700), (100.2, 100.4, 100.0, 100.1, 600), (100.1, 100.3, 99.9, 100.0, 500)]


def series_of(doc):
    return india.parse_chart(doc)


def now(hh, mm, day=DAY):
    return india.parse_now("%s %02d:%02d" % (day.isoformat(), hh, mm))


class Basics(unittest.TestCase):
    def test_rupees_use_indian_grouping(self):
        self.assertEqual(india.rupees(123456.5), "Rs 1,23,456.50")
        self.assertEqual(india.rupees(999), "Rs 999.00")
        self.assertEqual(india.rupees(10000000), "Rs 1,00,00,000.00")
        self.assertEqual(india.rupees(-480), "-Rs 480.00")

    def test_tick_rounding(self):
        self.assertEqual(india.to_tick(2412.37, 0.05), 2412.35)
        self.assertEqual(india.to_tick(2412.37, 0.05, up=True), 2412.40)
        self.assertEqual(india.to_tick(2412.35, 0.05), 2412.35)
        self.assertEqual(india.to_tick(99.75, 0.05), 99.75)
        self.assertEqual(india.to_tick(1.234, 0.01), 1.23)

    def test_india_time_has_no_daylight_saving(self):
        self.assertEqual(india.parse_now("2026-10-29 10:15"), datetime.datetime(2026, 10, 29, 4, 45))
        self.assertEqual(india.parse_now("2026-01-15 10:15"), datetime.datetime(2026, 1, 15, 4, 45))
        self.assertEqual(india.parse_now("2026-10-29T04:45:00Z"), datetime.datetime(2026, 10, 29, 4, 45))
        self.assertEqual(india.ts_to_ist(ts(DAY, 9, 15)), datetime.datetime(2026, 10, 29, 9, 15))

    def test_nse_calendar(self):
        self.assertEqual(india.trading_day(DAY), (True, ""))
        self.assertEqual(india.trading_day(datetime.date(2026, 11, 10)), (False, "NSE holiday: Diwali Balipratipada"))
        self.assertFalse(india.trading_day(datetime.date(2026, 10, 31))[0])  # Saturday
        self.assertEqual(len(india.NSE_HOLIDAYS), 16)
        for d in india.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertEqual(india.calendar_note(DAY), "")
        self.assertIn("covers 2026 only", india.calendar_note(datetime.date(2027, 1, 4)))

    def test_yahoo_symbols_carry_the_ns_suffix_and_escape_ampersands(self):
        self.assertIn("M%26M.NS", india.spark_path(["M&M", "TCS"], "3mo", "1d"))
        self.assertIn("TCS.NS", india.spark_path(["M&M", "TCS"], "3mo", "1d"))
        self.assertEqual(india.chart_path("BAJAJ-AUTO", "1d", "5m"), "/v8/finance/chart/BAJAJ-AUTO.NS?range=1d&interval=5m")
        self.assertEqual(india.from_yahoo("M&M.NS"), "M&M")


class Universe(unittest.TestCase):
    CSV = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
           "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,10,1,INE002A01018,10\n"
           "M&M,Mahindra & Mahindra Limited,EQ,17-NOV-1995,5,1,INE101A01026,5\n"
           "BAJAJ-AUTO,Bajaj Auto Limited,EQ,26-MAY-2008,10,1,INE917I01010,10\n"
           "TINYCO,Tiny Company Limited,BE,01-JAN-2020,10,1,INE000000000,10\n"
           "SMEX,SME Example Limited,BZ,01-JAN-2021,10,1,INE000000001,10\n")

    def test_series_eq_only(self):
        kept, counts = india.build_universe(self.CSV)
        self.assertEqual([s for s, _ in kept], ["BAJAJ-AUTO", "M&M", "RELIANCE"])
        self.assertEqual(counts["rows"], 5)
        self.assertEqual(counts["series BE"], 1)
        self.assertEqual(counts["series BZ"], 1)
        self.assertEqual(dict(kept)["M&M"], "Mahindra & Mahindra Limited")

    def test_not_the_nse_file(self):
        with self.assertRaises(india.IndiaError):
            india.build_universe("Symbol|Security Name\nAAPL|Apple\n")


class Daily(unittest.TestCase):
    def daily_series(self, closes, end=datetime.date(2026, 10, 28)):
        days, d = [], end
        while len(days) < len(closes):
            if india.trading_day(d)[0]:
                days.append(d)
            d -= datetime.timedelta(days=1)
        days.reverse()
        return {"t": [ts(x, 9, 15) for x in days], "c": list(closes),
                "meta": {"regularMarketPrice": closes[-1], "regularMarketVolume": 500000}}

    def test_breakout_on_closes(self):
        closes = [100 + 0.1 * (k % 5) for k in range(40)] + [103.0]
        data = {"ABC": self.daily_series(closes)}
        hits, counts, last = india.scan_daily(data, ["ABC", "GONE"], now(8, 30), 50, 100000)
        self.assertEqual(counts["fetched"], 1)
        self.assertEqual(hits[0]["symbol"], "ABC")
        self.assertIn("breakout_20d", hits[0]["setups"])
        self.assertEqual(last, datetime.date(2026, 10, 28))

    def test_todays_bar_waits_for_the_close(self):
        s = self.daily_series([100.0] * 40, end=DAY)
        self.assertEqual(india.daily_closes(s, now(15, 29))[-1][0], datetime.date(2026, 10, 28))
        self.assertEqual(india.daily_closes(s, now(15, 30))[-1][0], DAY)

    def test_price_floor_in_rupees(self):
        data = {"PENNY": self.daily_series([20.0] * 40)}
        hits, counts, _ = india.scan_daily(data, ["PENNY"], now(8, 30), 50, 0)
        self.assertEqual((counts["illiquid"], hits), (1, []))


class OpeningRange(unittest.TestCase):
    def test_hit_at_the_close_of_the_935_bar(self):
        s = series_of(chart("ABC", ORB_BARS))
        cutoff = datetime.datetime(2026, 10, 29, 9, 45)
        bars = india.session_bars(s, DAY, cutoff)
        self.assertEqual(len(bars), 6)
        self.assertTrue(india.usable(bars, DAY, cutoff, 3))
        h = india.orb_hit(bars, 3)
        self.assertEqual((h["at"], h["price"], h["or_high"], h["or_low"]), ("9:40 AM", 101.0, 100.5, 99.0))

    def test_scan_orb(self):
        data = {"ABC": series_of(chart("ABC", ORB_BARS)), "FLAT": series_of(chart("FLAT", FLAT_BARS))}
        day, cutoff = india.orb_window(now(9, 45), None, 15, datetime.time(14, 30))
        hits, counts, failed = india.scan_orb(data, ["ABC", "FLAT"], day, cutoff, 15)
        self.assertEqual([h["symbol"] for h in hits], ["ABC"])
        self.assertEqual((counts["scanned"], counts["candidates"]), (2, 1))  # FLAT never closes back at its range top

    def test_window_refusals(self):
        with self.assertRaisesRegex(india.IndiaError, "Run orb again after 9:35 AM"):
            india.orb_window(now(9, 31), None, 15, None)
        with self.assertRaisesRegex(india.IndiaError, "Diwali"):
            india.orb_window(now(10, 0, datetime.date(2026, 11, 10)), None, 15, None)
        with self.assertRaisesRegex(india.IndiaError, "no_entries_after"):
            india.orb_window(now(14, 45), None, 15, datetime.time(14, 30))

    def test_a_missing_bar_makes_a_chart_unusable(self):
        doc = chart("ABC", ORB_BARS)
        res = doc["chart"]["result"][0]
        del res["timestamp"][2]
        for k in res["indicators"]["quote"][0]:
            del res["indicators"]["quote"][0][k][2]
        bars = india.session_bars(series_of(doc), DAY, datetime.datetime(2026, 10, 29, 9, 45))
        self.assertFalse(india.usable(bars, DAY, datetime.datetime(2026, 10, 29, 9, 45), 3))


class Entry(unittest.TestCase):
    def test_states(self):
        s = series_of(chart("ABC", ORB_BARS))
        self.assertEqual(india.entry_status(s, now(9, 0))["status"], "not_open")
        self.assertEqual(india.entry_status(s, now(9, 25))["status"], "not_open")
        w = india.entry_status(s, now(9, 36))
        self.assertEqual((w["status"], w["line"], w["last"]), ("waiting", 100.5, 100.2))
        t = india.entry_status(s, now(9, 45))
        self.assertEqual((t["status"], t["entry"], t["entry_time"]), ("triggered", 101.0, "9:40 AM"))
        self.assertEqual(t["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15})
        x = india.entry_status(series_of(chart("FLAT", FLAT_BARS)), now(14, 45), until=datetime.time(14, 30))
        self.assertEqual(x["status"], "expired")
        h = india.entry_status(s, now(10, 0, datetime.date(2026, 11, 10)))
        self.assertEqual(h["status"], "not_open")

    def test_level(self):
        s = series_of(chart("ABC", ORB_BARS))
        self.assertEqual(india.entry_status(s, now(9, 45), level=101.05)["entry"], 101.1)


class PlanAndSize(unittest.TestCase):
    CFG = dict(india.DEFAULTS)

    def test_or_mid_stop_and_2r_target(self):
        p = india.compute_plan(101.0, "or-mid", None, "r", 2.0, 0.05, 100.5, 99.0)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"], p["reward_risk"]), (99.75, 1.25, 103.5, 2.0))

    def test_stop_rounds_down_and_target_rounds_down(self):
        p = india.compute_plan(2412.35, "pct", 1.0, "r", 1.5, 0.05)
        self.assertEqual(p["stop"], 2388.2)       # 2388.2265 down to the tick
        self.assertEqual(p["target"], 2448.55)    # 2412.35 + 1.5 x 24.15 = 2448.575 down to the tick
        self.assertAlmostEqual(p["risk_per_share"], 24.15)

    def test_bad_plans(self):
        with self.assertRaisesRegex(india.IndiaError, "at or above the entry"):
            india.compute_plan(100.0, "price", 100.0, "r", 2.0, 0.05)
        with self.assertRaisesRegex(india.IndiaError, "two ticks"):
            india.compute_plan(100.0, "price", 99.95, "r", 2.0, 0.05)
        with self.assertRaisesRegex(india.IndiaError, "at or below the entry"):
            india.compute_plan(100.0, "price", 99.0, "price", 99.5, 0.05)
        with self.assertRaisesRegex(india.IndiaError, "needs the opening range"):
            india.compute_plan(100.0, "or-mid", None, "r", 2.0, 0.05)

    def plan(self, entry, stop, target):
        return {"symbol": "ABC", "entry": entry, "stop": stop, "target": target, "date": DAY.isoformat(),
                "flat_by": "15:10", "setup": "orb"}

    def test_max_loss_binds_with_charges(self):
        order, limits, why = india.size_order(self.plan(101.0, 99.75, 103.5), self.CFG)
        # (1000 - 40 brokerage) / (1.25 risk + 0.0505 charges) = 738.1 -> 738 shares, but
        # the position cap (75,000 / 101 = 742) and cash (90,000 / 101 = 891) allow more
        self.assertEqual(order["qty"], 738)
        self.assertEqual(order["binds"], "max loss")
        self.assertLessEqual(order["max_loss_inr"], 1000)
        self.assertEqual(order["est_charges_inr"], round(40 + 0.0005 * 101.0 * 738, 2))

    def test_position_cap_binds_on_a_tight_stop(self):
        order, _, _ = india.size_order(self.plan(2400.0, 2399.0, 2402.0), self.CFG)
        self.assertEqual((order["qty"], order["binds"]), (31, "position cap"))   # 75,000 / 2,400

    def test_refuses_a_stop_too_far(self):
        order, _, why = india.size_order(self.plan(2400.0, 1300.0, 4600.0), self.CFG)
        self.assertIsNone(order)
        self.assertIn("too far", why)


class Risk(unittest.TestCase):
    CFG = dict(india.DEFAULTS)

    def order(self, **kw):
        o = {"symbol": "ABC", "side": "buy", "qty": 738, "type": "market", "entry": 101.0, "stop": 99.75,
             "target": 103.5, "date": DAY.isoformat()}
        o.update(kw)
        return o

    def test_pass_inside_the_window(self):
        v, reasons, _ = india.check_order(self.order(), self.CFG, None, now(10, 0))
        self.assertEqual((v, reasons), ("PASS", []))

    def test_window(self):
        self.assertIn("after your last entry time", " ".join(india.check_order(self.order(), self.CFG, None, now(14, 45))[1]))
        self.assertIn("before your first entry time", " ".join(india.check_order(self.order(), self.CFG, None, now(9, 20))[1]))
        self.assertIn("NSE is closed", " ".join(india.check_order(self.order(), self.CFG, None, now(16, 0))[1]))
        hol = india.check_order(self.order(date="2026-11-10"), self.CFG, None, now(10, 0, datetime.date(2026, 11, 10)))
        self.assertIn("Diwali", " ".join(hol[1]))

    def test_loss_and_reward_risk(self):
        v, reasons, _ = india.check_order(self.order(qty=800), self.CFG, None, now(10, 0))
        self.assertEqual(v, "BLOCK")
        self.assertTrue(any(r.startswith("loss:") for r in reasons))
        v, reasons, _ = india.check_order(self.order(qty=100, target=101.5), self.CFG, None, now(10, 0))
        self.assertTrue(any(r.startswith("reward:risk") for r in reasons))

    def test_day_limits_and_total_loss(self):
        log = {"trades": [{"date": DAY.isoformat(), "symbol": "XYZ", "max_loss_inr": 1500, "pnl_inr": None}]}
        v, reasons, _ = india.check_order(self.order(), self.CFG, log, now(10, 0))
        self.assertTrue(any(r.startswith("daily loss") for r in reasons))
        log = {"trades": [{"date": "2026-10-20", "symbol": "XYZ", "max_loss_inr": 1000, "pnl_inr": -2600},
                          {"date": "2026-10-27", "symbol": "XYZ", "max_loss_inr": 1000, "pnl_inr": -2400}]}
        v, reasons, _ = india.check_order(self.order(), self.CFG, log, now(10, 0))
        self.assertTrue(any(r.startswith("total loss") for r in reasons))
        log["trades"][1]["pnl_inr"] = -3500
        v, reasons, notes = india.check_order(self.order(qty=100), self.CFG, log, now(10, 0))
        self.assertEqual(v, "BLOCK")

    def test_no_stop_and_stale(self):
        self.assertIn("stop: no stop, and require_stop is on",
                      india.check_order(self.order(stop=None), self.CFG, None, now(10, 0))[1])
        self.assertTrue(any(r.startswith("stale") for r in
                            india.check_order(self.order(date="2026-10-28"), self.CFG, None, now(10, 0))[1]))


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        with open("ABC.json", "w") as fh:
            json.dump(chart("ABC", ORB_BARS), fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def run_cli(self, *args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = india.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_entry_to_ticket(self):
        t = ["--now", "2026-10-29 09:46"]
        code, text, err = self.run_cli("entry", "ABC", "--offline", "ABC.json", *t)
        self.assertEqual(code, 0, err)
        self.assertIn("TRIGGERED at 9:40 AM", text)
        code, text, err = self.run_cli("plan", *t)
        self.assertEqual(code, 0, err)
        self.assertIn("Stop 99.75", text)
        code, text, err = self.run_cli("size", *t)
        self.assertEqual(code, 0, err)
        self.assertIn("Size: 738 shares of ABC", text)
        code, text, err = self.run_cli("ticket", "show", *t)
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        code, text, err = self.run_cli("check", *t)
        self.assertEqual(code, 0, text + err)
        self.assertIn("PASS: buy 738 ABC", text)
        code, text, err = self.run_cli("ticket", "show", *t)
        self.assertEqual(code, 0)
        self.assertIn("PAPER TRADE", text)
        self.assertIn("READY: risk check passed today.", text)
        self.assertIn("SELL 738 ABC stop order, trigger 99.75", text)
        self.assertIn("never places an order", text)
        code, _, err = self.run_cli("ticket", "placed", *t)
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        code, text, err = self.run_cli("ticket", "placed", "--yes", *t)
        self.assertEqual(code, 0, err)
        code, text, err = self.run_cli("check", *t)
        self.assertEqual(code, 1)
        self.assertIn("duplicate", text)
        code, text, err = self.run_cli("ticket", "result", "ABC", "--pnl", "-950", *t)
        self.assertEqual(code, 0, err)
        self.assertIn("Running total: -Rs 950.00", text)
        code, text, err = self.run_cli("status", *t)
        self.assertIn("Today: 1 of 2 trades", text)
        self.assertIn("Rs 4,050.00 left", text)

    def test_a_dead_network_is_not_no_setups(self):
        def down(path):
            raise india.IndiaError("Yahoo answered Tunnel connection failed: 403 Forbidden for /v7/finance/spark")
        old = india.yahoo_get
        india.yahoo_get = down
        try:
            code, text, err = self.run_cli("daily", "--symbols", "RELIANCE,TCS", "--pause", "0",
                                           "--now", "2026-10-29 08:30")
            self.assertEqual(code, 1)
            self.assertIn("fetched 0 of 2 charts, so nothing was scanned", err)
            self.assertIn("403 Forbidden", err)
            self.assertNotIn("No setups", text)
            self.assertFalse(os.path.exists("trader/india/watchlist.json"))
        finally:
            india.yahoo_get = old

    def test_config_overrides_and_comments(self):
        os.makedirs("trader/india")
        with open("trader/india/config.json", "w") as fh:
            json.dump({"_note": "comments are ignored", "max_loss_per_trade_inr": 500}, fh)
        cfg = india.read_config("trader/india/config.json")
        self.assertEqual(cfg["max_loss_per_trade_inr"], 500)
        self.assertNotIn("_note", cfg)
        self.assertEqual(cfg["budget_inr"], 100000)

    def test_example_config_is_the_defaults(self):
        with open(os.path.join(ROOT, "trader-india", "config.example.json")) as fh:
            ex = json.load(fh)
        self.assertEqual({k: v for k, v in ex.items() if not k.startswith("_")}, india.DEFAULTS)
        self.assertIs(ex["paper"], True)
        self.assertIs(ex["allow_margin"], False)


if __name__ == "__main__":
    unittest.main()
