import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import load
from test_india import ORB_BARS, chart

india = load("trader-india", "india")

NOW = ["--now", "2026-10-29 09:46"]
CSV = ("EXCH,SEGMENT,SECURITY_ID,INSTRUMENT_NAME,EXPIRY_CODE,TRADING_SYMBOL,LOT_UNITS,CUSTOM_SYMBOL,EXPIRY_DATE,"
       "STRIKE_PRICE,OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SERIES,SYMBOL_NAME\n"
       "NSE,E,4242,EQUITY,0,ABC-EQ,1,ABC Ltd,,,,0.05,,ES,EQ,ABC\n"
       "BSE,E,999001,EQUITY,0,ABC,1,ABC Ltd,,,,0.05,,ES,A,ABC\n"
       "NSE,E,4243,EQUITY,0,XYZ-BE,1,XYZ Ltd,,,,0.05,,ES,BE,XYZ\n")


class FakeINDstocks(object):
    """Answers like the INDstocks API docs show, and records every call."""

    def __init__(self):
        self.calls = []
        self.fail_post = None
        self.book = []
        self.positions = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split("api.indstocks.com", 1)[1].split("?")[0]
        self.calls.append((method, path, body, headers.get("Authorization")))
        if method == "POST" and self.fail_post:
            raise self.fail_post
        if path == "/market/instruments":
            return 200, CSV
        if path == "/smart/order" and method == "POST":
            return 200, json.dumps({"status": "success", "data": {"order_data": [
                {"order_id": "EQ-1001", "order_status": "CREATED",
                 "child_order_details": {"order_id": "GTT-2001", "order_status": "CREATED"}}]}})
        if path == "/order-book":
            return 200, json.dumps({"status": "success", "data": self.book})
        if path == "/portfolio/positions":
            return 200, json.dumps({"status": "success", "data": self.positions})
        if path == "/market/quotes/ltp":
            return 200, json.dumps({"status": "success", "data": {"NSE_4242": {"live_price": 102.4}}})
        if path == "/order" and method == "POST":
            return 200, json.dumps({"status": "success", "data": {"order_id": "EQ-1002", "order_status": "INITIATED"}})
        if path in ("/smart/order/cancel", "/order/cancel"):
            return 200, json.dumps({"status": "success", "data": {"order_id": body["order_id"], "order_status": "CANCELLED"}})
        if path == "/user/profile":
            return 200, json.dumps({"status": "success", "data": {"user_id": "7", "is_nse_onboarded": True}})
        if path == "/funds":
            return 200, json.dumps({"status": "success", "data": {"detailed_avl_balance": {"eq_mis": 98000}}})
        return 404, json.dumps({"status": "error", "error_type": "NotFoundException", "message": "no"})

    def posts(self, path=None):
        return [c for c in self.calls if c[0] == "POST" and (path is None or c[1] == path)]


class Broker(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self.fake = FakeINDstocks()
        self.old_http = india.HTTP
        india.HTTP = self.fake
        self.old_env = os.environ.get(india.TOKEN_ENV)
        os.environ[india.TOKEN_ENV] = "tok-123"
        os.makedirs("trader/india")
        self.config(broker="indstocks", paper=False)
        with open("ABC.json", "w") as fh:
            json.dump(chart("ABC", ORB_BARS), fh)

    def tearDown(self):
        india.HTTP = self.old_http
        if self.old_env is None:
            os.environ.pop(india.TOKEN_ENV, None)
        else:
            os.environ[india.TOKEN_ENV] = self.old_env
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def config(self, **kw):
        with open("trader/india/config.json", "w") as fh:
            json.dump(kw, fh)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = india.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def chain(self):
        for step in (["entry", "ABC", "--offline", "ABC.json"], ["plan"], ["size"], ["check"]):
            code, text, err = self.run_cli(*(step + NOW))
            self.assertEqual(code, 0, " ".join(step) + ": " + text + err)

    def test_sizing_counts_the_stop_leg_limit(self):
        cfg = dict(india.DEFAULTS, broker="indstocks")
        self.assertEqual(india.sl_limit(99.75, cfg), 99.25)   # 0.5 % under 99.75 = 99.25125, down to the tick
        plan = {"symbol": "ABC", "entry": 101.0, "stop": 99.75, "target": 103.5, "date": "2026-10-29"}
        order, _, _ = india.size_order(plan, cfg)
        manual, _, _ = india.size_order(plan, dict(india.DEFAULTS))
        self.assertEqual(manual["qty"], 738)
        self.assertLess(order["qty"], 738)
        self.assertLessEqual(order["max_loss_inr"], 1000)
        self.assertEqual(order["qty"], int((1000 - 40) / (101.0 - 99.25 + 0.0005 * 101.0)))

    def test_smart_order_body(self):
        cfg = dict(india.DEFAULTS, broker="indstocks")
        order = {"symbol": "ABC", "side": "buy", "qty": 523, "type": "market", "entry": 101.0, "stop": 99.75,
                 "target": 103.5, "date": "2026-10-29"}
        b = india.smart_order_body(order, {"security_id": "4242", "tick": 0.05}, cfg)
        self.assertEqual((b["txn_type"], b["exchange"], b["segment"], b["product"], b["order_type"], b["validity"]),
                         ("BUY", "NSE", "EQUITY", "INTRADAY", "LIMIT", "DAY"))
        self.assertEqual((b["security_id"], b["qty"], b["limit_price"]), ("4242", 523, 101.0))
        self.assertEqual((b["sl_trigger_price"], b["sl_limit_price"]), (99.75, 99.25))
        self.assertEqual((b["tgt_trigger_price"], b["tgt_limit_price"]), (103.5, 103.55))
        self.assertTrue(b["remarks"].startswith("trader-india/"))
        self.assertNotIn("is_tsl", b)
        with self.assertRaisesRegex(india.IndiaError, "upper circuit"):
            india.smart_order_body(order, {"security_id": "4242", "upper": 100.0}, cfg)

    def test_instruments_keep_nse_eq_only(self):
        inst = india.parse_instruments(CSV)
        self.assertEqual(sorted(inst), ["ABC"])
        self.assertEqual(inst["ABC"]["security_id"], "4242")

    def test_preview_sends_nothing(self):
        self.chain()
        code, text, err = self.run_cli("broker", "preview", *NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("LIVE order for INDstocks (POST /smart/order), would be sent", text)
        self.assertIn("security_id 4242", text)
        self.assertEqual(self.fake.posts(), [])
        self.assertEqual(self.fake.calls[0][3], "tok-123")

    def test_send_needs_the_users_yes(self):
        self.chain()
        for reply in ([], ["--reply", "ok go"], ["--reply", "yes please do it"]):
            code, _, err = self.run_cli("broker", "send", *(reply + NOW))
            self.assertEqual(code, 1)
            self.assertIn("yes", err)
        self.assertEqual(self.fake.posts(), [])

    def test_paper_sends_nothing(self):
        self.config(broker="indstocks", paper=True)
        self.chain()
        code, text, err = self.run_cli("broker", "send", "--reply", "yes", *NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("NOTHING was sent", text)
        self.assertEqual(self.fake.posts(), [])

    def test_live_send_logs_and_never_twice(self):
        self.chain()
        code, text, err = self.run_cli("broker", "send", "--reply", "Yes", *NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("Sent. Order EQ-1001", text)
        self.assertIn("GTT-2001", text)
        posts = self.fake.posts("/smart/order")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["limit_price"], 101.0)
        with open("trader/india/state/trades.json") as fh:
            t = json.load(fh)["trades"][0]
        self.assertEqual((t["broker_order_id"], t["paper"]), ("EQ-1001", False))
        with open("trader/india/state/broker.json") as fh:
            self.assertEqual(json.load(fh)["orders"][0]["reply"], "Yes")
        code, _, err = self.run_cli("broker", "send", "--reply", "yes", *NOW)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.fake.posts("/smart/order")), 1)

    def test_no_send_without_todays_pass(self):
        for step in (["entry", "ABC", "--offline", "ABC.json"], ["plan"], ["size"]):
            self.run_cli(*(step + NOW))
        code, _, err = self.run_cli("broker", "send", "--reply", "yes", *NOW)
        self.assertEqual(code, 1)
        self.assertIn("did not pass", err)
        self.assertEqual(self.fake.posts(), [])

    def test_no_send_once_the_window_has_closed(self):
        self.chain()
        code, _, err = self.run_cli("broker", "send", "--reply", "yes", "--now", "2026-10-29 14:45")
        self.assertEqual(code, 1)
        self.assertIn("after your last entry time", err)
        self.assertEqual(self.fake.posts(), [])

    def test_a_lost_answer_is_never_resent(self):
        self.chain()
        self.fake.fail_post = OSError("timed out")
        code, _, err = self.run_cli("broker", "send", "--reply", "yes", *NOW)
        self.assertEqual(code, 1)
        self.assertIn("do not resend", err)
        self.fake.fail_post = None
        code, _, err = self.run_cli("broker", "send", "--reply", "yes", *NOW)
        self.assertEqual(code, 1)
        self.assertIn("already sent", err)
        self.assertEqual(len(self.fake.posts("/smart/order")), 1)

    def test_close_cancels_the_legs_first(self):
        self.fake.book = [{"id": "GTT-2001", "security_id": "4242", "status": "PENDING"},
                          {"id": "GTT-9", "security_id": "4242", "status": "CANCELLED"},
                          {"id": "EQ-1001", "security_id": "4242", "status": "SUCCESS"}]
        self.fake.positions = [{"security_id": "4242", "symbol": "ABC", "net_qty": 523}]
        code, text, err = self.run_cli("broker", "close", "ABC", "--reply", "yes", *NOW)
        self.assertEqual(code, 0, err)
        posts = self.fake.posts()
        self.assertEqual([p[1] for p in posts], ["/smart/order/cancel", "/order"])
        self.assertEqual(posts[0][2], {"order_id": "GTT-2001", "segment": "EQUITY"})
        sell = posts[1][2]
        self.assertEqual((sell["txn_type"], sell["qty"], sell["limit_price"], sell["product"]),
                         ("SELL", 523, 101.85, "INTRADAY"))   # 0.5 % under the last price 102.40

    def test_cancel_picks_the_endpoint(self):
        self.fake.book = [{"id": "EQ-1001", "child_order_id": "GTT-2001"}, {"id": "EQ-1002"}]
        for oid in ("GTT-2001", "EQ-1001", "EQ-1002"):
            code, _, err = self.run_cli("broker", "cancel", oid, "--reply", "yes", *NOW)
            self.assertEqual(code, 0, err)
        self.assertEqual([p[1] for p in self.fake.posts()],
                         ["/smart/order/cancel", "/smart/order/cancel", "/order/cancel"])

    def test_guards(self):
        os.environ.pop(india.TOKEN_ENV)
        code, _, err = self.run_cli("broker", "orders", *NOW)
        self.assertIn("INDSTOCKS_TOKEN", err)
        os.environ[india.TOKEN_ENV] = "tok"
        self.config(broker="manual")
        code, _, err = self.run_cli("broker", "send", "--reply", "yes", *NOW)
        self.assertEqual(code, 1)
        self.assertIn('"broker": "indstocks"', err)
        self.assertEqual(self.fake.posts(), [])

    def test_setup_checks_the_token(self):
        code, text, err = self.run_cli("broker", "setup", *NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("Token works: account 7, NSE enabled", text)
        self.assertIn("STATIC IP", text)

    def test_api_errors_read_clearly(self):
        def denied(method, url, headers, body, timeout):
            return 403, json.dumps({"status": "error", "error_type": "TokenException", "message": "Invalid token"})
        india.HTTP = denied
        with self.assertRaisesRegex(india.IndiaError, "HTTP 403.*TokenException Invalid token.*make a new one"):
            india.ind_call("GET", "/funds", "tok")


if __name__ == "__main__":
    unittest.main()
