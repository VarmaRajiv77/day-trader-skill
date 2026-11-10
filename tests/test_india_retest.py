import datetime
import json
import unittest

from _load import load
from test_india import DAY, ORB_BARS, chart, history_chart, now

india = load("trader-india", "india")

# made-up bars: the 9:35 bar breaks the 100.50 range high, then...
RETEST = ORB_BARS[:5] + [(101.0, 101.0, 100.52, 100.70, 900), (100.7, 101.3, 100.6, 101.2, 800)]   # dips to 100.52, closes 100.70
FAILED = ORB_BARS[:5] + [(101.0, 101.0, 99.70, 99.90, 900), (99.9, 100.6, 99.8, 100.6, 800)]       # falls to the midpoint 99.75
NO_PULLBACK = ORB_BARS[:5] + [(101.0, 101.6, 100.9, 101.5, 900), (101.5, 101.9, 101.3, 101.8, 800)]


def status(bars, hh, mm, rule="retest"):
    return india.entry_status(india.parse_chart(chart("ABC", bars)), now(hh, mm), 15, datetime.time(14, 30), None, rule, 0.05)


class BreakAndRetest(unittest.TestCase):
    def test_retest_triggers_on_the_pullback_close(self):
        s = status(RETEST, 9, 51)
        self.assertEqual((s["status"], s["entry"], s["entry_time"], s["broke_at"]), ("triggered", 100.7, "9:45 AM", "9:40 AM"))
        self.assertEqual(s["entry_bar"], 5)
        self.assertEqual(status(RETEST, 9, 51, "break")["entry"], 101.0)   # the old rule still buys the break

    def test_waiting_for_the_retest(self):
        s = status(RETEST, 9, 41)   # only the break bar (9:35 to 9:40) has closed
        self.assertEqual(s["status"], "retest_wait")
        self.assertIn("waiting for a pullback to 100.50", s["why"])
        self.assertEqual(status(NO_PULLBACK, 9, 51)["status"], "retest_wait")

    def test_a_fall_to_the_midpoint_fails_it(self):
        s = status(FAILED, 9, 51)
        self.assertEqual(s["status"], "failed")
        self.assertIn("fell back to the middle of the range", s["why"])

    def test_find_retest(self):
        bars = [{"h": 1, "l": 1, "c": 1}] * 4 + [{"h": 11, "l": 10, "c": 10.8}, {"h": 10.9, "l": 10.02, "c": 10.5}]
        self.assertEqual(india.find_retest(bars, 4, 10.0, 9.0, 5, 0.5), (5, None))
        self.assertEqual(india.find_retest(bars, 4, 10.0, 9.0, 5, 0.1), (None, None))   # 10.02 is 0.2 % away, over 0.1 %


class History(unittest.TestCase):
    def test_retest_history_trades_no_more_than_break(self):
        s = india.parse_chart(history_chart("ABC"))
        sessions = india.full_sessions(india.tm_days(s, now(9, 50)), DAY.isoformat())
        base = {"or_minutes": 15, "stop_rule": "or-mid", "target_r": 2.0}
        brk = india.history_replay(sessions, dict(base, entry_rule="break"), 70, 0.05)
        ret = india.history_replay(sessions, dict(base, entry_rule="retest", retest_tol_pct=0.05), 70, 0.05)
        self.assertLessEqual(ret["triggered"], brk["triggered"])
        self.assertEqual(ret["rule"]["entry_rule"], "retest")


class TopDown(unittest.TestCase):
    def test_trend_and_levels_and_the_filter(self):
        idx = india.parse_chart(history_chart("NSEI"))
        cfg = dict(india.DEFAULTS)
        side = india.nifty_side(idx, "NIFTY 50", now(9, 46), cfg, 2.0, 200)
        t, lv = side["trend"], side["levels"]
        self.assertEqual(t["as_of"], "2026-10-28")
        self.assertEqual(t["up"], t["close"] > t["ma20"])
        self.assertLessEqual(lv["week_low"], lv["prev_low"])
        self.assertGreaterEqual(lv["week_high"], lv["prev_high"])
        filtered = india.nifty_side(idx, "NIFTY 50", now(9, 46), dict(cfg, trend_filter=True), 2.0, 200)
        if t["up"]:
            self.assertIn("plan", filtered)
        else:
            self.assertNotIn("plan", filtered)
            self.assertIn("your trend filter says sit out", filtered["plan_note"])


if __name__ == "__main__":
    unittest.main()
