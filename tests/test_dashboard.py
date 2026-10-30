import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import load

dash = load("trader-dashboard", "dashboard")

NOW = datetime.datetime(2026, 10, 19, 14, 16)   # 10:16 AM New York, 7:46 PM India

# the reel's TSLA trade as the US skills write it (trader-exit, -entry, -timemachine, -budget, -risk)
US = {
    "plan": {"symbol": "TSLA", "date": "2026-10-19", "entry": 364.18, "entry_time": "10:15 AM", "stop": 358.32,
             "stop_label": "opening-range midpoint", "target": 370.04, "target_label": "1R (one times the risk)",
             "flat_by": "15:55", "risk_per_share": 5.86},
    "entry": {"symbol": "TSLA", "status": "triggered", "time": "10:15 AM", "price": 364.18},
    "timemachine": {"plan": {"symbol": "TSLA", "date": "2026-10-19"},
                    "mc": {"p_target": 0.2938, "p_stop": 0.2548, "p_neither": 0.4514, "until": "16:00", "replays": 10000},
                    "history": {"triggered": 33, "target": 13, "stop": 15, "close": 5},
                    "verdict": "Close to a coin flip: target first 29.4 % vs stop first 25.5 %."},
    "orders": {"orders": [{"symbol": "TSLA", "side": "buy", "qty": "8", "stop_loss_stop_price": "358.32",
                           "take_profit_limit_price": "370.04"}]},
    "risk": {"date": "2026-10-19", "results": [{"symbol": "TSLA", "verdict": "PASS", "reasons": []}]},
    "watchlist": {"mode": "orb", "as_of": "2026-10-19 10:15 ET", "hits": [
        {"symbol": "AMC", "price": 2.97, "at": "9:50 AM", "or_high": 2.96, "or_low": 2.90, "setups": ["orb"]},
        {"symbol": "TSLA", "price": 364.18, "at": "10:15 AM", "or_high": 362.18, "or_low": 354.47, "setups": ["orb"]}]},
}

# a made-up NSE trade as trader-india writes it
INDIA = {
    "plan": {"symbol": "ABC", "date": "2026-10-19", "entry": 101.0, "entry_time": "9:40 AM", "stop": 99.75,
             "stop_label": "opening-range midpoint", "target": 103.5, "target_label": "2R, 2 times the risk",
             "flat_by": "15:10"},
    "entry": {"symbol": "ABC", "status": "triggered", "entry_time": "9:40 AM", "entry": 101.0},
    "timemachine": {"symbol": "ABC", "plan": {"date": "2026-10-19"},
                    "mc": {"p_target": 0.096, "p_stop": 0.516, "p_neither": 0.388, "until": "15:10", "replays": 10000},
                    "history": None, "verdict": "Strongly toward the stop: ..."},
    "orders": {"orders": [{"symbol": "ABC", "qty": 738, "value_inr": 74538.0, "est_charges_inr": 77.27,
                           "max_loss_inr": 999.77, "gain_at_target_inr": 1767.73}]},
    "risk": {"date": "2026-10-19", "results": [{"symbol": "ABC", "verdict": "PASS", "reasons": []}]},
}


class Card(unittest.TestCase):
    def test_us_numbers(self):
        c = dash.card_data(US, "us", "2026-10-19")
        self.assertEqual((c["symbol"], c["entry"], c["stop"], c["target"], c["flat_by"]),
                         ("TSLA", 364.18, 358.32, 370.04, "15:55"))
        self.assertEqual((c["qty"], c["value"], c["max_loss"], c["risk_share"]), (8, 2913.44, 46.88, 5.86))
        self.assertIsNone(c["charges"])
        self.assertEqual(c["signal"], "10:15 AM")
        self.assertEqual(c["risk"]["verdict"], "PASS")
        self.assertAlmostEqual(c["odds"]["target"], 0.2938)
        self.assertFalse(c["old"])

    def test_india_numbers(self):
        c = dash.card_data(INDIA, "india", "2026-10-19")
        self.assertEqual((c["qty"], c["charges"], c["max_loss"]), (738, 77.27, 999.77))
        self.assertEqual(c["signal"], "9:40 AM")

    def test_mismatches_do_not_count(self):
        d = dict(US, risk={"date": "2026-10-18", "results": [{"symbol": "TSLA", "verdict": "PASS"}]},
                 timemachine=dict(US["timemachine"], plan={"symbol": "NVDA", "date": "2026-10-19"}))
        c = dash.card_data(d, "us", "2026-10-19")
        self.assertNotIn("risk", c)
        self.assertNotIn("odds", c)
        self.assertTrue(dash.card_data(US, "us", "2026-10-29")["old"])

    def test_nothing_yet(self):
        self.assertNotIn("symbol", dash.card_data({}, "us", "2026-10-19"))


class Page(unittest.TestCase):
    def page(self, us=US, india=INDIA, **kw):
        return dash.render(NOW, [("us", us), ("india", india)], **kw)

    def test_the_card_rows_in_order(self):
        p = self.page()
        rows = ["Stock", "Buy at", "Stop-loss", "Target", "Close-out time", "Time Machine odds", "Shares",
                "Charges", "Max loss at the stop", "Risk check"]
        us = p[p.index("US stocks"):p.index("Indian stocks")]
        at = [us.index("<th>%s</th>" % r) for r in rows]
        self.assertEqual(at, sorted(at))
        for want in ("364.18", "358.32", "370.04", "3:55 PM ET", "29.4%", "$2,913.44", "$46.88", "PASS",
                     "not estimated", "BUY 8 TSLA at market", "SELL 8 at a stop of 358.32", "13 hit the target"):
            self.assertIn(want, us)
        ind = p[p.index("Indian stocks"):]
        for want in ("3:10 PM IST", "Rs 74,538.00", "Rs 77.27", "Rs 999.77", "BUY 738 NSE ABC",
                     "leans toward the stop for this plan"):
            self.assertIn(want, ind)
        self.assertIn("PAPER TRADE", p)
        self.assertIn("Not picks", p)
        self.assertIn("All options from the scan: 2 charts", p)

    def test_missing_steps_say_how(self):
        p = self.page(us={"plan": US["plan"]}, india={})
        self.assertIn("not run yet: <code>/trader-timemachine</code>", p)
        self.assertIn("not run yet: <code>/trader-risk</code>", p)
        self.assertIn("No trade planned yet", p)
        self.assertNotIn("The order to type", p)

    def test_block_shows_reasons_and_no_order(self):
        risk = {"date": "2026-10-19", "results": [{"symbol": "TSLA", "verdict": "BLOCK",
                                                   "reasons": ["loss: $58.60 over your $50.00 max loss"]}]}
        p = self.page(us=dict(US, risk=risk), india={})
        self.assertIn("BLOCK", p)
        self.assertIn("loss: $58.60 over your $50.00 max loss", p)
        self.assertNotIn("The order to type", p)

    def test_notes_are_escaped(self):
        p = dash.render(NOW, [("us", US)], notes={"us": "Replay <b>"})
        self.assertIn("Replay &lt;b&gt;", p)


class Options(unittest.TestCase):
    def test_every_breakout_gets_levels_and_shares(self):
        opt = dash.options(US, "us", dict(dash.US_LIMITS))
        self.assertEqual([r["symbol"] for r in opt["rows"]], ["AMC", "TSLA"])   # the scan's order, not ranked
        t = opt["rows"][1]
        # stop at the range's midpoint (358.325 down to the cent), target 2R, $50 max loss -> 8 shares
        self.assertEqual((t["entry"], t["stop"], t["risk_share"], t["target"]), (364.18, 358.32, 5.86, 375.9))
        self.assertEqual((t["qty"], t["max_loss"], t["charges"]), (8, 46.88, None))
        amc = opt["rows"][0]
        self.assertEqual((amc["stop"], amc["target"]), (2.93, 3.05))
        self.assertEqual(amc["qty"], 1250)   # capped by 50 / 0.04 = 1,250 shares
        self.assertEqual(opt["date"], "2026-10-19")

    def test_india_uses_the_tick_and_charges(self):
        d = {"watchlist": {"mode": "orb", "as_of": "2026-10-19 09:46 IST", "hits": [
            {"symbol": "ABC", "price": 101.0, "at": "9:40 AM", "or_high": 100.5, "or_low": 99.0}]}}
        r = dash.options(d, "india", dict(dash.INDIA_LIMITS))["rows"][0]
        self.assertEqual((r["stop"], r["target"], r["qty"]), (99.75, 103.5, 738))
        self.assertEqual(r["charges"], round(40 + 0.0005 * 101.0 * 738, 2))
        self.assertLessEqual(r["max_loss"], 1000)

    def test_daily_hits_have_no_levels(self):
        d = {"watchlist": {"mode": "daily", "as_of": "2026-10-16 close",
                           "hits": [{"symbol": "KO", "price": 93.9, "setups": ["breakout_20d"]}]}}
        r = dash.options(d, "us", dict(dash.US_LIMITS))["rows"][0]
        self.assertNotIn("entry", r)
        self.assertIn("no breakout levels", r["note"])

    def test_a_stop_too_far_is_zero_shares(self):
        d = {"watchlist": {"as_of": "2026-10-19 10:15 ET", "hits": [
            {"symbol": "BIG", "price": 1300.0, "at": "10:00 AM", "or_high": 1290.0, "or_low": 1100.0}]}}
        r = dash.options(d, "us", dict(dash.US_LIMITS))["rows"][0]
        self.assertEqual(r["qty"], 0)
        self.assertIn("too far", r["note"])

    def test_odds_match_only_the_same_plan(self):
        rep = {"plan": {"date": "2026-10-19", "entry": 364.18}, "verdict": "Close to break-even",
               "mc": {"p_target": 0.2, "p_stop": 0.4, "p_neither": 0.4}}
        d = dict(US, replays={"TSLA": rep, "AMC": dict(rep, plan={"date": "2026-10-18", "entry": 2.97})})
        rows = dash.options(d, "us", dict(dash.US_LIMITS))["rows"]
        self.assertEqual(rows[1]["odds"]["target"], 0.2)
        self.assertIsNone(rows[0]["odds"])

    def test_table_and_commands(self):
        p = dash.render(NOW, [("us", US)])
        self.assertIn("All options from the scan: 2 charts", p)
        self.assertIn("Not ranked and not picks", p)
        self.assertIn("375.90", p)
        self.assertIn('not run', p)
        lines = dash.replay_commands(dash.options(US, "us", dict(dash.US_LIMITS)), "tm.py")
        self.assertEqual(len(lines), 2)
        self.assertIn('run --symbol TSLA --date 2026-10-19 --entry 364.18 --stop 358.32 --target 375.90 '
                      '--entry-time "10:15 AM" --stop-rule or_mid --write trader/replays/TSLA.json', lines[1])


class Cli(unittest.TestCase):
    def test_writes_the_page(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(d)
            os.makedirs("trader/state")
            os.makedirs("trader/india/state")
            for name, doc in US.items():
                with open("trader/%s%s.json" % ("state/" if name == "risk" else "", name), "w") as fh:
                    json.dump(doc, fh)
            with open("trader/india/plan.json", "w") as fh:
                fh.write("{not json")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = dash.main(["--now", "2026-10-19T14:16:00Z"])
            self.assertEqual(code, 0)
            with open("trader/dashboard.html", encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn("TSLA", page)
            self.assertIn("No trade planned yet", page)   # a broken India file reads as not run
            with open("trader/options.json") as fh:
                self.assertEqual(len(json.load(fh)["rows"]), 2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                dash.main(["--replay-commands", "--now", "2026-10-19T14:16:00Z"])
            self.assertIn("--write trader/replays/AMC.json", out.getvalue())
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
