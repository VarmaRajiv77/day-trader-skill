import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import load
from test_india import history_chart

india = load("trader-india", "india")
dash = load("trader-dashboard", "dashboard")


def series(sym):
    """Made-up 5-minute history (30 full sessions, then the 29 Oct 2026 breakout), not market data."""
    return india.parse_chart(history_chart(sym))


class Desk(unittest.TestCase):
    CFG = dict(india.DEFAULTS)

    def desk(self, when, replays=500):
        return india.nifty_desk(series("NSEI"), series("NIFTYBEES"), self.CFG, india.parse_now(when), 2.0, replays)

    def test_open_market_plan_odds_size_and_check(self):
        doc = self.desk("2026-10-29 09:46")
        i, e = doc["index"], doc["etf"]
        self.assertEqual((i["name"], i["phase"]), ("NIFTY 50", "open"))
        self.assertEqual(i["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15})
        self.assertEqual((i["signal"]["status"], i["signal"]["entry_time"]), ("triggered", "9:40 AM"))
        self.assertEqual((i["plan"]["entry"], i["plan"]["stop"], i["plan"]["target"]), (101.1, 99.75, 103.8))
        self.assertEqual(i["timemachine"]["mc"]["replays"], 500)
        self.assertEqual(len(i["daily"]["setups"]) >= 0, True)
        self.assertEqual(e["name"], "NIFTYBEES")
        self.assertTrue(e["order"]["qty"] > 0)
        self.assertLessEqual(e["order"]["max_loss_inr"], 1000)
        self.assertEqual(e["risk"]["verdict"], "PASS")
        self.assertEqual(e["risk"]["fingerprint"], india.fingerprint(e["order"]))

    def test_no_plan_before_the_breakout(self):
        doc = self.desk("2026-10-29 09:36")          # the 9:35 bar has not closed above the range yet
        i = doc["index"]
        self.assertEqual(i["signal"]["status"], "waiting")
        self.assertNotIn("plan", i)
        self.assertIn("no plan until a 5-minute bar closes above the opening range high 100.50", i["plan_note"])
        self.assertNotIn("order", doc["etf"])

    def test_no_plan_once_price_is_back_under_the_line(self):
        from test_india import ORB_BARS, chart
        bars = ORB_BARS + [(101.1, 101.1, 100.2, 100.3, 900)]      # triggered at 9:40, then back under 100.50
        idx = india.parse_chart(chart("NSEI", bars))
        side = india.nifty_side(idx, "NIFTY 50", india.parse_now("2026-10-29 09:51"), self.CFG, 2.0, 100)
        self.assertEqual(side["signal"]["status"], "triggered")
        self.assertNotIn("plan", side)
        self.assertIn("back under the range high 100.50", side["plan_note"])

    def test_weekend_is_a_summary_with_no_plan(self):
        doc = self.desk("2026-10-31 18:00")
        i = doc["index"]
        self.assertEqual((i["phase"], i["phase_why"]), ("closed", "weekend"))
        self.assertNotIn("plan", i)
        self.assertNotIn("order", doc["etf"])
        self.assertEqual(i["date"], "2026-10-29")          # the last day in the data (a short one here)
        self.assertIsNotNone(i["prev_close"])
        self.assertAlmostEqual(i["change"], round(i["last"] - i["prev_close"], 2))
        self.assertEqual(i["daily"]["as_of"], "2026-10-28")

    def test_too_early_and_too_late(self):
        self.assertEqual(self.desk("2026-10-29 09:20")["index"]["phase"], "forming")
        late = self.desk("2026-10-29 14:45")["index"]
        self.assertEqual(late["phase"], "late")
        self.assertNotIn("plan", late)
        self.assertEqual(self.desk("2026-11-10 10:00")["index"]["phase_why"], "NSE holiday: Diwali Balipratipada")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        with open("NSEI.json", "w") as fh:
            json.dump(history_chart("NSEI"), fh)
        os.makedirs("etf")
        with open("etf/NIFTYBEES.json", "w") as fh:
            json.dump(history_chart("NIFTYBEES"), fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = india.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_writes_the_desk_and_the_trade_files(self):
        code, text, err = self.run_cli("nifty", "--offline-index", "NSEI.json", "--offline-etf", "etf",
                                       "--replays", "300", "--now", "2026-10-29 09:46")
        self.assertEqual(code, 0, err)
        for want in ("Market open, entries allowed", "NIFTY 50 101.10 at 9:45 AM", "Opening range (9:15 to 9:30): 99.00 to 100.50",
                     "Breakout signal: closed above the range at 9:40 AM", "Time Machine: target first",
                     "Sized to your limits", "Risk check: PASS", "not predictions"):
            self.assertIn(want, text)
        for f in ("nifty.json", "plan.json", "timemachine.json", "orders.json", "state/risk.json"):
            self.assertTrue(os.path.exists("trader/india/" + f), f)
        code, text, _ = self.run_cli("ticket", "show", "--now", "2026-10-29 09:47")
        self.assertEqual(code, 0)
        self.assertIn("READY: risk check passed today", text)
        self.assertIn("NIFTYBEES", text)
        page = dash.render(india.parse_now("2026-10-29 09:47"), [("india", dash.load_market("trader/india", "india"))])
        self.assertIn("<h2>Nifty 50</h2>", page)
        self.assertIn("TRIGGERED", page)
        self.assertLess(page.index("<h2>Nifty 50</h2>"), page.index("<h2>Indian stocks</h2>"))

    def test_weekend_writes_only_the_summary(self):
        code, text, err = self.run_cli("nifty", "--offline-index", "NSEI.json", "--offline-etf", "etf",
                                       "--now", "2026-10-31 18:00")
        self.assertEqual(code, 0, err)
        self.assertIn("Market closed: weekend.", text)
        self.assertIn("Run it again after 9:35 AM IST", text)
        self.assertTrue(os.path.exists("trader/india/nifty.json"))
        self.assertFalse(os.path.exists("trader/india/orders.json"))
        page = dash.render(india.parse_now("2026-11-02 10:00"), [("india", dash.load_market("trader/india", "india"))])
        self.assertIn("OLD: this is from 2026-10-31", page)


if __name__ == "__main__":
    unittest.main()
