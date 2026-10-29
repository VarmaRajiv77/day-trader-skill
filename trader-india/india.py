#!/usr/bin/env python3
"""The India Desk for trader-india: one day trade on NSE, in rupees. Standard library only.

    python3 india.py universe                          # NSE's equity list -> trader/india/universe.txt
    python3 india.py daily                             # before 9:15 AM IST: daily setups -> watchlist.json
    python3 india.py daily --symbols RELIANCE,TCS,INFY # or --watchlist FILE, --limit 200
    python3 india.py orb                               # after 9:35 AM IST: opening-range breakouts so far
    python3 india.py entry RELIANCE                    # waiting / triggered / expired -> entry.json
    python3 india.py plan --stop or-mid --target 2R    # stop and target -> plan.json
    python3 india.py replay                            # the Time Machine -> timemachine.json
    python3 india.py replay-all                        # ... on every breakout in the scan -> replays/
    python3 india.py size                              # shares for your rupee limits -> orders.json
    python3 india.py check                             # PASS / BLOCK -> state/risk.json
    python3 india.py ticket show                       # the order to type into your broker's app
    python3 india.py ticket placed --yes               # you placed it: log it for the day limits
    python3 india.py ticket result RELIANCE --pnl -480 # how it ended: counts toward the total limit
    python3 india.py status                            # today's trades, today's risk, the running total

Everything lives in trader/india/ so it never mixes with the US files in trader/.

The market. NSE's cash session runs 9:15 AM to 3:30 PM India time (IST, UTC+5:30,
no daylight saving). The NSE equity holidays for 2026 are built in; outside 2026
every weekday counts as a trading day and the tool says so, so check NSE's
holiday circular. Muhurat trading (one evening session on Diwali) is not modelled.

The data. Yahoo Finance's free chart endpoints carry NSE stocks as SYMBOL.NS
(RELIANCE.NS, M&M.NS). They are unofficial, can lag and can rate-limit you.
The universe comes from NSE's own equity list (EQUITY_L.csv): series EQ only
(BE / BZ trade-to-trade stocks cannot be day traded). NSE sometimes refuses
scripts; then download the file in a browser and pass --offline FILE.

universe  NSE EQUITY_L.csv -> SYMBOL|Name, series EQ.
daily     Pre-market scan of daily CLOSES (Yahoo spark, 20 symbols a request,
          3 months of 1-day bars). Floors: --min-price 50 (rupees) and
          --min-volume 100,000 shares. The setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
orb       Opening-range breakouts on 5-minute bars. The range is the first
          --or-minutes (15) of the session: the 9:15, 9:20 and 9:25 bars. A hit
          is the first completed bar to CLOSE above the range high. Stage 1:
          spark closes for every symbol; stage 2: the v8 chart (real highs and
          lows) for the --max-confirm (200) most traded candidates.
entry     One stock, bar by bar: not open yet / waiting (and how far) /
          triggered (the time and the price) / expired (after no_entries_after).
plan      Stop: or-mid (default), or-low, a price, or N%. Target: 2R (default),
          NR, or a price. The stop rounds DOWN and the target DOWN to the tick
          (tick_size, 0.05). Refuses a stop at or above the entry.
replay    The Time Machine. 60 days of 5-minute bars (one request); the pool is
          every full past session (75 bars, 9:15 to 3:30). Each of 10,000
          replays walks from the entry bar to the flat-by time, drawing every
          bar whole from the same time of day (+-2 bars) as ratios to the last
          close; the stop is checked first in each bar. Then the same rule on
          every past session. Prints the odds, the history and one verdict:
          the target's share of the replays that hit a side against the share
          that breaks even at the target's R (50 % at 1R, 33 % at 2R), under 5
          points a coin flip / break-even, 5 to 15 leans, 15+ strongly. Needs
          20 full sessions. Seed 19 by default: same data, same numbers.
size      Whole shares inside four limits, the smallest wins: max loss per
          trade (the loss at the stop PLUS estimated charges), the position
          cap, the max order and cash after your buffer. No margin.
check     PASS or BLOCK against your rules, and today's log (state/trades.json).
ticket    The order in plain words, READY only if THIS order passed today.
          This tool never places an order: you type it into your broker's app.

--now "2026-10-29 10:15" pins the clock (India time; ...Z for UTC).
--offline PATH reads saved files instead of the network.

Education and tooling, not financial advice. A scan is a list of charts that
match a rule, not a list of stocks to buy. Exit code 0 on success / a hit /
a PASS, 1 otherwise (the reason is one plain line).
"""

import argparse
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NSE_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SUFFIX = ".NS"
SPARK_MAX = 20
PAUSE = 0.3
BACKOFF = 5.0
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200
MIN_DAILY_BARS = 30
DIR = "trader/india"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
SAFETY = "Education and tooling, not financial advice."

IST = datetime.timedelta(hours=5, minutes=30)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)

# NSE equity segment trading holidays, 2026 (NSE's holiday circular as carried by
# brokers' calendars, read 2026-10-29). Weekend festivals are left out.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal corporation elections in Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)

DEFAULTS = {
    "budget_inr": 100000,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 75000,
    "min_order_inr": 500,
    "max_loss_per_trade_inr": 1000,
    "max_daily_loss_inr": 2000,
    "total_loss_limit_inr": 5000,
    "max_trades_per_day": 2,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "flat_by": "15:10",
    "tick_size": 0.05,
    "brokerage_per_order_inr": 20,
    "charges_pct_round_trip": 0.05,
    "paper": True,
    "blocked": [],
    "allow_margin": False,
    "allow_short": False,
}

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class IndiaError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ts_to_ist(ts):
    """Unix seconds -> naive India time. Never uses the machine's time zone."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)) + IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise IndiaError('--now wants a time like "2026-10-29 10:15" (India time) or ...Z (UTC)')
    if dt.tzinfo is None:
        return dt - IST
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise IndiaError("%s wants an India time like 10:15" % what)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def trading_day(d):
    """(is_trading_day, reason). Weekends and the NSE holiday list."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def calendar_note(d):
    if d.year in CALENDAR_YEARS:
        return ""
    return ("The built-in NSE holiday list covers %s only: %s is treated as a trading day because it is a "
            "weekday. Check NSE's holiday circular." % (", ".join(str(y) for y in CALENDAR_YEARS), d.isoformat()))


def clock_label(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- money

def rupees(x):
    """Indian digit grouping: 123456.5 -> 'Rs 1,23,456.50'."""
    neg = x < 0
    whole, frac = ("%.2f" % abs(x)).split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return "%sRs %s.%s" % ("-" if neg else "", whole, frac)


def _p(x):
    return "{:,.2f}".format(x)


def to_tick(x, tick, up=False):
    """Round to the exchange tick. Down unless up=True. Works in paise to dodge float dust."""
    paise = int(round(float(tick) * 100))
    if paise <= 0:
        raise IndiaError("tick_size must be a positive price like 0.05")
    v = float(x) * 100
    n = math.ceil(v / paise - 1e-9) if up else math.floor(v / paise + 1e-9)
    return round(n * paise / 100.0, 2)


def charges(cfg, price, qty):
    """Estimated round-trip costs of a day trade: brokerage on two orders plus a
    percent of the buy value for STT, exchange fees, stamp duty and GST. An estimate:
    your contract note has the real figure."""
    return round(2 * float(cfg["brokerage_per_order_inr"]) +
                 float(cfg["charges_pct_round_trip"]) / 100.0 * price * qty, 2)


# ---------------------------------------------------------------- files

def path(name):
    return os.path.join(DIR, name)


def read_json(p, what, required=True):
    if not os.path.exists(p):
        if required:
            raise IndiaError("no %s at %s: %s" % (what, p, {
                "plan": "run `india.py plan` first",
                "orders": "run `india.py size` first",
                "entry": "run `india.py entry SYMBOL` first, or give --entry"}.get(what, "")))
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise IndiaError("%s is not valid JSON: %s" % (p, e))


def write_json(p, doc):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def read_config(p):
    cfg = dict(DEFAULTS)
    doc = read_json(p, "config", required=False)
    if doc is not None:
        if not isinstance(doc, dict):
            raise IndiaError("%s is not a JSON object" % p)
        cfg.update({k: v for k, v in doc.items() if not k.startswith("_")})
    for k in ("no_entries_before", "no_entries_after", "flat_by"):
        parse_hhmm(cfg[k], k)
    for k in ("budget_inr", "max_loss_per_trade_inr", "max_order_inr"):
        if not isinstance(cfg[k], (int, float)) or cfg[k] <= 0:
            raise IndiaError("%s in %s must be a positive number of rupees" % (k, p))
    return cfg


# ---------------------------------------------------------------- network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(p):
    """query2 first, query1 on a 429 or 5xx, one more round after BACKOFF on a 429."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + p)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body
                raise IndiaError("Yahoo answered %s for %s" % (last, p.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise IndiaError("Yahoo answered %s for %s" % (last, p.split("?")[0]))


def to_yahoo(sym):
    return sym.upper() + SUFFIX


def from_yahoo(ysym):
    ysym = ysym.upper()
    return ysym[:-len(SUFFIX)] if ysym.endswith(SUFFIX) else ysym


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(urllib.parse.quote(to_yahoo(s), safe="") for s in symbols), rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(to_yahoo(sym), safe=""), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc):
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise IndiaError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        out[from_yahoo(ysym)] = series_from_result(resp[0])
    if err and not out:
        raise IndiaError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise IndiaError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise IndiaError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(p):
    if p.endswith(".gz"):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _absorb(doc, hint, data, errors):
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise IndiaError("an offline v8 chart without a symbol: name the file <SYMBOL>.json")
        sym = from_yahoo(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except IndiaError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise IndiaError("offline file %s is not a spark answer, a v8 chart or a bundle" % hint)


def load_offline(p):
    """A folder of <SYMBOL>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(p):
        names = sorted(f for f in os.listdir(p) if f.endswith((".json", ".json.gz")))
        if not names:
            raise IndiaError("no .json files in %s" % p)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(p, f)))
            except ValueError as e:
                raise IndiaError("%s is not valid JSON: %s" % (f, e))
            stem = f[:-8] if f.endswith(".json.gz") else f[:-5]
            if isinstance(doc, dict) and "chart" in doc:
                _absorb(doc, stem, data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(p):
        try:
            doc = json.loads(_read_maybe_gz(p))
        except ValueError as e:
            raise IndiaError("%s is not valid JSON: %s" % (p, e))
        _absorb(doc, "", data, errors)
    else:
        raise IndiaError("no offline file or folder at %s" % p)
    return data, errors


# ---------------------------------------------------------------- universe

def build_universe(text):
    """NSE EQUITY_L.csv text -> (kept [(SYMBOL, name)], counts). Series EQ only."""
    rd = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        hdr = [h.strip().upper() for h in next(rd)]
    except StopIteration:
        raise IndiaError("the NSE equity list is empty")
    if "SYMBOL" not in hdr or "SERIES" not in hdr:
        raise IndiaError("not NSE's EQUITY_L.csv (no SYMBOL / SERIES columns)")
    i_sym, i_ser = hdr.index("SYMBOL"), hdr.index("SERIES")
    i_name = hdr.index("NAME OF COMPANY") if "NAME OF COMPANY" in hdr else None
    kept, counts = {}, {"rows": 0}
    for row in rd:
        if not row or not any(c.strip() for c in row):
            continue
        counts["rows"] += 1
        sym = row[i_sym].strip().upper() if len(row) > i_sym else ""
        ser = row[i_ser].strip().upper() if len(row) > i_ser else ""
        if ser != "EQ":
            k = "series %s" % (ser or "blank")
            counts[k] = counts.get(k, 0) + 1
            continue
        if not re.match(r"^[A-Z0-9&\-]{1,20}$", sym):
            counts["odd symbol"] = counts.get("odd symbol", 0) + 1
            continue
        kept[sym] = " ".join(row[i_name].split()) if i_name is not None and len(row) > i_name else ""
    return sorted(kept.items()), counts


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_closes(series, now_utc):
    """[(date, close)] of COMPLETE daily bars; today's bar waits for the 3:30 PM close."""
    now = to_ist(now_utc)
    today = now.date()
    ok, _ = trading_day(today)
    today_done = not ok or now.time() >= CLOSE
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)
    return sorted(out.items())


def find_setups(c):
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r}


def scan_daily(data, symbols, now_utc, min_price, min_volume):
    """Pure. -> (hits, counts, last_date). Hits most traded first (rupee turnover)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_closes(s, now_utc)
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        if price is None or price < min_price or (min_volume > 0 and (vol is None or vol < min_volume)):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(vol) if vol is not None else None})
    hits.sort(key=lambda h: (-(h["price"] * (h["volume"] or 0)), h["symbol"]))
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening range

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` complete at `cutoff` (naive IST), on the grid, with a close."""
    out = {}
    hs, ls, vs = series.get("h"), series.get("l"), series.get("v")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        t = ts_to_ist(ts)
        if t.date() != day or t.second or t.minute % BAR_MIN:
            continue
        if t.time() < OPEN or t.time() >= CLOSE:
            continue
        if t + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue
        out[t] = {"start": t, "c": float(c),
                  "h": None if not hs or hs[i] is None else float(hs[i]),
                  "l": None if not ls or ls[i] is None else float(ls[i]),
                  "v": (vs[i] or 0) if vs else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range):
    """Every slot from 9:15 to the cutoff is there (a gap is a halt or no trades)."""
    end = min(cutoff, datetime.datetime.combine(day, CLOSE))
    want = int((end - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def opening_range(bars, n_range):
    rng = bars[:n_range]
    if len(rng) < n_range or any(b["h"] is None or b["l"] is None for b in rng):
        return None
    return max(b["h"] for b in rng), min(b["l"] for b in rng)


def orb_hit(bars, n_range, level=None):
    """The first completed bar after the range to CLOSE above the range high (or `level`)."""
    r = opening_range(bars, n_range)
    if r is None:
        return None
    oh, ol = r
    top = oh if level is None else float(level)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > top:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at), "_t": closes_at,
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / top - 1) * 100, 2)}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise IndiaError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise IndiaError("No NSE session on %s (%s). Run orb on a trading day after the opening range, "
                         "or replay a past day with --offline." % (day_label(day), why))
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, CLOSE)
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise IndiaError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise IndiaError("--at %s is after the 3:30 PM close." % at.strftime("%H:%M"))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise IndiaError("NSE closed at 3:30 PM IST on %s. Replay the session with --at 10:00."
                             % day_label(day))
    if cutoff < first_close:
        raise IndiaError("It is %s IST: the %d-minute opening range runs 9:15 to %s and the first bar after "
                         "it closes at %s. Run orb again after %s."
                         % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                            clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise IndiaError("It is %s IST, after %s, your last time for new entries (no_entries_after): a "
                         "breakout found now is expired." % (clock_label(cutoff), until.strftime("%H:%M")))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0, confirm=None,
             max_confirm=None):
    """Pure but for confirm(sym) -> series with highs and lows. -> (hits, counts, failed)."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, bars[-1]["c"] * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                full = session_bars(confirm(sym), day, cutoff)
            except IndiaError as e:
                failed[sym] = str(e)
                continue
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- entry

def entry_status(series, now_utc, or_minutes=15, until=None, level=None):
    """One stock: not_open / waiting / triggered / expired. Pure."""
    now = to_ist(now_utc)
    day = now.date()
    n_range = or_minutes // BAR_MIN
    ok, why = trading_day(day)
    base = {"date": day.isoformat(), "or_minutes": or_minutes}
    if not ok:
        return dict(base, status="not_open", why="no NSE session (%s)" % why)
    cutoff = now.replace(second=0, microsecond=0)
    range_end = slot(day, n_range)
    if cutoff < range_end:
        why = "the market opens at 9:15 AM IST" if cutoff.time() < OPEN else "the opening range is still forming"
        return dict(base, status="not_open", why="%s; the range is complete at %s" % (why, clock_label(range_end)))
    bars = session_bars(series, day, cutoff)
    r = opening_range(bars, n_range)
    if r is None or bars[0]["start"] != slot(day, 0):
        return dict(base, status="no_data", why="the opening-range bars are missing (a halt, or no trades yet)")
    oh, ol = r
    base.update(opening_range={"high": round(oh, 2), "low": round(ol, 2), "minutes": or_minutes},
                level=round(float(level), 2) if level is not None else None)
    h = orb_hit(bars, n_range, level)
    if h:
        late = until is not None and h["_t"].time() > until
        return dict(base, status="expired" if late else "triggered", entry=h["price"], entry_time=h["at"],
                    entry_bar=h["bar_index"], pct_over=h["pct_over"],
                    why="triggered after no_entries_after" if late else "")
    if until is not None and cutoff.time() > until:
        return dict(base, status="expired", why="no close above the line before %s" % until.strftime("%H:%M"))
    if cutoff.time() >= CLOSE:
        return dict(base, status="expired", why="the session closed without a trigger")
    last = bars[-1]["c"]
    top = oh if level is None else float(level)
    return dict(base, status="waiting", last=round(last, 2), line=round(top, 2),
                away_pct=round((top / last - 1) * 100, 2), last_bar=clock_label(bars[-1]["start"]))


# ---------------------------------------------------------------- plan

def parse_stop(text):
    t = str(text or "or-mid").strip().lower()
    if t in ("or-mid", "or-low"):
        return t, None
    if t.endswith("%"):
        return "pct", float(t[:-1])
    try:
        return "price", float(t)
    except ValueError:
        raise IndiaError("--stop wants or-mid, or-low, a price like 2412.50, or a percent like 1.5%")


def parse_target(text):
    t = str(text or "2R").strip().upper()
    if t.endswith("R"):
        try:
            return "r", float(t[:-1])
        except ValueError:
            pass
    try:
        return "price", float(t)
    except ValueError:
        raise IndiaError("--target wants a multiple of the risk like 2R, or a price")


def compute_plan(entry, stop_rule, stop_param, target_rule, target_param, tick, or_high=None, or_low=None):
    """-> dict with stop, target, risk_per_share, reward_risk. Raises on a bad plan."""
    entry = float(entry)
    if stop_rule in ("or-mid", "or-low"):
        if or_high is None or or_low is None:
            raise IndiaError("%s needs the opening range: run `india.py entry SYMBOL` or pass --or-high and --or-low"
                             % stop_rule)
        raw = (or_high + or_low) / 2.0 if stop_rule == "or-mid" else or_low
        label = "opening-range midpoint" if stop_rule == "or-mid" else "opening-range low"
    elif stop_rule == "pct":
        if not 0 < stop_param < 50:
            raise IndiaError("a percent stop wants a number between 0 and 50")
        raw = entry * (1 - stop_param / 100.0)
        label = "%g%% under the entry" % stop_param
    else:
        raw, label = stop_param, "your price"
    stop = to_tick(raw, tick)
    if stop >= entry:
        raise IndiaError("the stop %s is at or above the entry %s: a long trade needs the stop below it"
                         % (_p(stop), _p(entry)))
    if stop <= 0:
        raise IndiaError("the stop must be above zero")
    risk = round(entry - stop, 2)
    if risk < 2 * float(tick):
        raise IndiaError("the stop is %s under the entry, under two ticks: the spread alone could hit it" % _p(risk))
    if target_rule == "r":
        if target_param <= 0:
            raise IndiaError("the target multiple must be above zero")
        target = to_tick(entry + target_param * risk, tick)
        tlabel = "%gR, %g times the risk" % (target_param, target_param)
    else:
        target = to_tick(target_param, tick)
        tlabel = "your price"
    if target <= entry:
        raise IndiaError("the target %s is at or below the entry %s" % (_p(target), _p(entry)))
    return {"entry": round(entry, 2), "stop": stop, "stop_label": label, "target": target, "target_label": tlabel,
            "risk_per_share": risk, "reward_risk": round((target - entry) / risk, 2)}


# ---------------------------------------------------------------- size

def size_order(plan, cfg):
    """Whole shares inside four limits. -> (order or None, limits [(name, how, qty)], why)."""
    entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
    risk = entry - stop
    budget = float(cfg["budget_inr"])
    fixed = 2 * float(cfg["brokerage_per_order_inr"])
    pct = float(cfg["charges_pct_round_trip"]) / 100.0
    max_loss = float(cfg["max_loss_per_trade_inr"])
    cap = budget * float(cfg["max_position_pct"]) / 100.0
    cash = budget * (1 - float(cfg["cash_buffer_pct"]) / 100.0)
    room = max_loss - fixed
    per = risk + pct * entry
    limits = [
        ("max loss", "(%s - %s brokerage) / (%s risk + %s charges) a share"
         % (rupees(max_loss), rupees(fixed), _p(risk), _p(pct * entry)),
         int(math.floor(room / per + 1e-9)) if room > 0 else 0),
        ("position cap", "%g%% of %s = %s / %s" % (cfg["max_position_pct"], rupees(budget), rupees(cap), _p(entry)),
         int(math.floor(cap / entry + 1e-9))),
        ("max order", "%s / %s" % (rupees(float(cfg["max_order_inr"])), _p(entry)),
         int(math.floor(float(cfg["max_order_inr"]) / entry + 1e-9))),
        ("cash", "%s minus the %g%% buffer = %s / %s" % (rupees(budget), cfg["cash_buffer_pct"], rupees(cash), _p(entry)),
         int(math.floor(cash / entry + 1e-9))),
    ]
    qty = min(q for _, _, q in limits)
    binds = [n for n, _, q in limits if q == qty][0]
    if qty < 1:
        if binds == "max loss":
            return None, limits, ("one share would lose %s at the stop with charges, over your %s max loss: the "
                                  "stop is too far for your limit. Find a tighter setup; do not raise the limit "
                                  "to fit the trade." % (rupees(risk + fixed + pct * entry), rupees(max_loss)))
        return None, limits, "one share costs %s, over your %s limit" % (rupees(entry), binds)
    value = round(qty * entry, 2)
    est = charges(cfg, entry, qty)
    order = {"symbol": plan["symbol"], "exchange": "NSE", "side": "buy", "qty": qty, "type": "market",
             "entry": entry, "stop": stop, "target": target, "value_inr": value, "est_charges_inr": est,
             "loss_at_stop_inr": round(qty * risk, 2), "max_loss_inr": round(qty * risk + est, 2),
             "gain_at_target_inr": round(qty * (target - entry) - est, 2), "flat_by": plan.get("flat_by"),
             "date": plan.get("date"), "setup": plan.get("setup"), "binds": binds}
    return order, limits, ""


# ---------------------------------------------------------------- risk check

def fingerprint(order):
    keys = ("symbol", "side", "qty", "type", "entry", "stop", "target", "date")
    blob = json.dumps([order.get(k) for k in keys], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def trades_today(log, day):
    return [t for t in (log or {}).get("trades", []) if t.get("date") == day.isoformat()]


def realized_total(log):
    return round(sum(float(t["pnl_inr"]) for t in (log or {}).get("trades", []) if t.get("pnl_inr") is not None), 2)


def check_order(order, cfg, log, now_utc):
    """-> (verdict, reasons [str], notes [str]). Pure."""
    now = to_ist(now_utc)
    day = now.date()
    reasons, notes = [], []
    sym = str(order.get("symbol") or "").upper()
    if not sym or order.get("side") != "buy" or not isinstance(order.get("qty"), int) or order["qty"] < 1:
        reasons.append("format: needs a symbol, side buy and a whole number of shares")
        return "BLOCK", reasons, notes
    if sym in [str(b).upper() for b in cfg.get("blocked") or []]:
        reasons.append("blocked: %s is on your blocked list" % sym)
    entry, qty = float(order["entry"]), order["qty"]
    stop, target = order.get("stop"), order.get("target")
    value = entry * qty
    if value < float(cfg["min_order_inr"]):
        reasons.append("size: %s is under min_order_inr %s" % (rupees(value), rupees(float(cfg["min_order_inr"]))))
    if value > float(cfg["max_order_inr"]):
        reasons.append("size: %s is over max_order_inr %s" % (rupees(value), rupees(float(cfg["max_order_inr"]))))
    budget = float(cfg["budget_inr"])
    if value > budget * float(cfg["max_position_pct"]) / 100.0:
        reasons.append("position: %s is over %g%% of your %s budget" % (rupees(value), cfg["max_position_pct"],
                                                                      rupees(budget)))
    if value > budget * (1 - float(cfg["cash_buffer_pct"]) / 100.0) and not cfg.get("allow_margin"):
        reasons.append("cash: %s would dip into your %g%% cash buffer (margin is off)"
                       % (rupees(value), cfg["cash_buffer_pct"]))
    if stop is None:
        if cfg.get("require_stop", True):
            reasons.append("stop: no stop, and require_stop is on")
        risk = None
    elif float(stop) >= entry:
        reasons.append("stop: %s is not below the entry %s" % (_p(float(stop)), _p(entry)))
        risk = None
    else:
        risk = entry - float(stop)
    est = charges(cfg, entry, qty)
    if risk is not None:
        loss = qty * risk + est
        if loss > float(cfg["max_loss_per_trade_inr"]) + 0.005:
            reasons.append("loss: %d x %s + %s charges = %s, over your %s max loss a trade"
                           % (qty, _p(risk), rupees(est), rupees(loss), rupees(float(cfg["max_loss_per_trade_inr"]))))
        if target is None or float(target) <= entry:
            reasons.append("reward:risk: no target above the entry")
        else:
            rr = (qty * (float(target) - entry) - est) / loss
            if rr + 1e-9 < float(cfg["min_reward_risk"]):
                reasons.append("reward:risk: %.2f after charges, under your minimum %g" % (rr, cfg["min_reward_risk"]))
        today = trades_today(log, day)
        used = sum(float(t.get("max_loss_inr") or 0) for t in today)
        if used + loss > float(cfg["max_daily_loss_inr"]) + 0.005:
            reasons.append("daily loss: %s already at risk today + %s = %s, over your %s a day"
                           % (rupees(used), rupees(loss), rupees(used + loss), rupees(float(cfg["max_daily_loss_inr"]))))
        if len(today) >= int(cfg["max_trades_per_day"]):
            reasons.append("trades: %d trade%s already today (max_trades_per_day %d)"
                           % (len(today), "" if len(today) == 1 else "s", int(cfg["max_trades_per_day"])))
        if any(str(t.get("symbol")).upper() == sym for t in today):
            reasons.append("duplicate: %s was already traded today" % sym)
        total = realized_total(log)
        limit = float(cfg.get("total_loss_limit_inr") or 0)
        if limit and -total >= limit - 0.005:
            reasons.append("total loss: %s lost so far, at your %s limit. Stop and review before trading again."
                           % (rupees(-total), rupees(limit)))
        elif limit and -total + loss > limit + 0.005:
            notes.append("if this stops out, your total loss would pass your %s limit (%s so far)"
                         % (rupees(limit), rupees(-total)))
    ok, why = trading_day(day)
    t = now.time()
    if not ok:
        reasons.append("window: no NSE session today (%s)" % why)
    elif t < OPEN or t >= CLOSE:
        reasons.append("window: NSE is closed (9:15 AM to 3:30 PM IST); it is %s" % clock_label(now))
    else:
        if t < parse_hhmm(cfg["no_entries_before"]):
            reasons.append("window: before your first entry time %s" % cfg["no_entries_before"])
        if t > parse_hhmm(cfg["no_entries_after"]):
            reasons.append("window: after your last entry time %s" % cfg["no_entries_after"])
    if order.get("date") and order["date"] != day.isoformat():
        reasons.append("stale: the order is for %s, today is %s; make a new plan" % (order["date"], day.isoformat()))
    note = calendar_note(day)
    if note:
        notes.append(note)
    return ("BLOCK" if reasons else "PASS"), reasons, notes


# ---------------------------------------------------------------- time machine

SESSION_BARS = 75    # 9:15 to 3:30 PM in 5-minute bars
MIN_SESSIONS = 20
COIN_FLIP = 0.05
STRONG = 0.15


def tm_days(series, now_utc):
    """{date: {slot: bar}} of complete regular-session 5-minute bars, India time."""
    days = {}
    for i, ts in enumerate(series["t"]):
        o, h, l, c = [(series.get(k) or [None] * len(series["t"]))[i] for k in ("o", "h", "l", "c")]
        if ts is None or None in (o, h, l, c) or int(ts) % 300:
            continue
        t = ts_to_ist(ts)
        mins = t.hour * 60 + t.minute - (OPEN.hour * 60 + OPEN.minute)
        if mins < 0 or mins >= SESSION_BARS * BAR_MIN:
            continue
        if t + datetime.timedelta(minutes=BAR_MIN) > to_ist(now_utc):
            continue
        days.setdefault(t.date().isoformat(), {})[mins // BAR_MIN] = {
            "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
    return days


def full_sessions(days, before):
    """Full sessions (all 75 bars) strictly before `before`, oldest first."""
    return [(d, [days[d][i] for i in range(SESSION_BARS)]) for d in sorted(days)
            if d < before and len(days[d]) == SESSION_BARS]


def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close."""
    pool = [[] for _ in range(SESSION_BARS)]
    for _, D in sessions:
        for i in range(1, SESSION_BARS):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2):
    """Replays of bars k+1..last, each bar drawn whole from the same time of day (+-window
    bars) in past sessions and scaled to the current price. The stop is checked first in
    every bar, so a bar that touches both counts as the stop."""
    risk = entry - stop
    r_target = round((target - entry) / risk, 4)
    rng = random.Random(seed)
    outcomes, closes = [], []
    for _ in range(replays):
        p, outcome = entry, None
        for i in range(k + 1, last + 1):
            j = i if window == 0 else min(SESSION_BARS - 1, max(1, i + rng.randint(-window, window)))
            o, h, l, c = rng.choice(pool[j])
            if outcome is None:
                if p * l <= stop:
                    outcome = "stop"
                elif p * h >= target:
                    outcome = "target"
            p *= c
        outcomes.append(outcome or "close")
        closes.append(p)
    n = float(replays)
    srt = sorted(closes)
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else (p - entry) / risk
          for o, p in zip(outcomes, closes)]
    return {"replays": replays, "seed": seed, "window": window, "bars_left": last - k,
            "p_target": round(outcomes.count("target") / n, 4), "p_stop": round(outcomes.count("stop") / n, 4),
            "p_neither": round(outcomes.count("close") / n, 4),
            "p_green": round(sum(1 for o, p in zip(outcomes, closes)
                                 if o == "target" or (o == "close" and p > entry)) / n, 4),
            "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
            "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3), "r_at_target": r_target}


def history_replay(sessions, rule, last, tick):
    """The plan's opening-range rule on every past full session: entry = the first bar after
    the range to close above it (up to the last bar), then stop first, then target."""
    n = rule["or_minutes"] // BAR_MIN
    trades = []
    for d, D in sessions:
        oh, ol = max(b["h"] for b in D[:n]), min(b["l"] for b in D[:n])
        kk = next((i for i in range(n, last + 1) if D[i]["c"] > oh), None)
        if kk is None:
            continue
        e = round(D[kk]["c"], 2)
        raw = (oh + ol) / 2 if rule["stop_rule"] == "or-mid" else ol if rule["stop_rule"] == "or-low" \
            else e * (1 - rule["stop_pct"] / 100.0)
        st = to_tick(raw, tick)
        risk = e - st
        if risk <= 0:
            continue
        tg = to_tick(e + rule["target_r"] * risk, tick)
        oc = None
        for b in D[kk + 1:last + 1]:
            if b["l"] <= st:
                oc = "stop"
                break
            if b["h"] >= tg:
                oc = "target"
                break
        R = (tg - e) / risk if oc == "target" else -1.0 if oc == "stop" else (D[last]["c"] - e) / risk
        trades.append({"date": d, "entry": e, "stop": st, "target": tg, "outcome": oc or "close", "R": round(R, 2)})
    return {"sessions": len(sessions), "triggered": len(trades),
            "target": sum(1 for x in trades if x["outcome"] == "target"),
            "stop": sum(1 for x in trades if x["outcome"] == "stop"),
            "close": sum(1 for x in trades if x["outcome"] == "close"),
            "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
            "rule": rule, "trades": trades}


def verdict(mc, until_label):
    """Compares the target's share of the replays that hit a side with the share that breaks
    even at the target's R (50 % at 1R, 33 % at 2R)."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return "No replay reached the target or the stop by %s: the price then decides every one." % until_label
    r = mc["r_at_target"] or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    d = share - even
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; it did in "
            "%.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def bar_of(clock):
    """'9:40 AM' (a bar's close time) -> the bar's index from 9:15."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM)?\s*$", str(clock or ""), re.I)
    if not m:
        raise IndiaError("an entry time wants a form like 9:40 AM or 09:40")
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    k = (h * 60 + mi - (OPEN.hour * 60 + OPEN.minute)) // BAR_MIN - 1
    if not 0 <= k < SESSION_BARS:
        raise IndiaError("the entry time %s is outside the 9:15 AM to 3:30 PM session" % clock)
    return k


def time_machine(series, plan, now_utc, replays=10000, seed=19, window=2, until=None):
    if not 1 <= replays <= 1000000:
        raise IndiaError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise IndiaError("--window must be between 0 and 10 bars")
    if not plan.get("entry_time"):
        raise IndiaError("the plan has no entry time: give --entry-time like 9:40 AM")
    k = plan.get("entry_bar")
    k = bar_of(plan["entry_time"]) if k is None else int(k)
    u = parse_hhmm(until or plan.get("flat_by") or "15:30", "--until")
    until_min = u.hour * 60 + u.minute
    open_min = OPEN.hour * 60 + OPEN.minute
    if (until_min - open_min) % BAR_MIN or until_min > CLOSE.hour * 60 + CLOSE.minute:
        raise IndiaError("--until must be on the 5-minute grid and no later than 15:30")
    last = (until_min - open_min) // BAR_MIN - 1
    if last <= k:
        raise IndiaError("%s leaves nothing to replay after the %s entry" % (u.strftime("%H:%M"), plan["entry_time"]))
    days = tm_days(series, now_utc)
    sessions = full_sessions(days, plan["date"])
    if len(sessions) < MIN_SESSIONS:
        raise IndiaError("only %d full session(s) of %s before %s in the data; the time machine needs at least %d"
                         % (len(sessions), plan["symbol"], plan["date"], MIN_SESSIONS))
    entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
    mc = simulate(build_pool(sessions), entry, stop, target, k, last, replays, seed, window)
    until_label = clock_label(u)
    mc.update(until=u.strftime("%H:%M"), pool_sessions=len(sessions), pool_from=sessions[0][0],
              pool_to=sessions[-1][0])
    history = None
    rng = plan.get("opening_range") or {}
    if plan.get("setup") == "orb" and rng.get("minutes"):
        srule = plan.get("stop_rule") if plan.get("stop_rule") in ("or-mid", "or-low") else "pct"
        rule = {"or_minutes": int(rng["minutes"]), "stop_rule": srule, "target_r": mc["r_at_target"]}
        if srule == "pct":
            rule["stop_pct"] = round(100.0 * (entry - stop) / entry, 4)
        history = history_replay(sessions, rule, last, float(plan.get("tick_size") or 0.05))
    return {"created_by": "trader-india", "mode": "timemachine",
            "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": plan["symbol"], "exchange": "NSE",
            "plan": {kk: plan.get(kk) for kk in ("date", "entry", "entry_time", "stop", "target", "setup")},
            "entry_bar": k, "mc": mc, "history": history, "verdict": verdict(mc, until_label),
            "note": ("A simulation built from %s's own last %d full sessions. It is not a prediction: results, "
                     "news, circuits and halts can do things those sessions never did. Charges are not in the "
                     "R figures. Same seed, same numbers." % (plan["symbol"], len(sessions)))}


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None):
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)))
        except (IndiaError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 50 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def nothing_fetched(failed, n):
    """Raise when not one chart came back, so a dead network never reads as 'no setups'."""
    why = sorted(set((failed or {}).values()))
    raise IndiaError("fetched 0 of %d charts, so nothing was scanned (%s). Check the connection to Yahoo "
                     "Finance, or pass --offline." % (n, why[0] if why else "no data"))


def symbols_from_file(p):
    if not os.path.exists(p):
        raise IndiaError("no file at %s" % p)
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise IndiaError("%s is not valid JSON: %s" % (p, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    return [ln.split("|")[0].strip().upper() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [from_yahoo(s) for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)
    else:
        p = a.universe or path("universe.txt")
        if not os.path.exists(p):
            raise IndiaError("no %s yet: run `india.py universe` first, or pass --symbols" % p)
        syms = symbols_from_file(p)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise IndiaError("no symbols to scan")
    return out


def load_one(sym, offline, rng="1d"):
    if offline:
        data, errors = load_offline(offline)
        if sym in data:
            return data[sym]
        raise IndiaError(errors.get(sym) or "no chart for %s in %s" % (sym, offline))
    return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN)), sym)


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    if a.offline:
        if not os.path.isfile(a.offline):
            raise IndiaError("no file at %s (want NSE's EQUITY_L.csv)" % a.offline)
        with open(a.offline, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        try:
            text = http_get(NSE_EQUITY_URL, timeout=60)
        except urllib.error.HTTPError as e:
            raise IndiaError("NSE answered HTTP %d for EQUITY_L.csv: download it in a browser from %s and pass "
                             "--offline FILE" % (e.code, NSE_EQUITY_URL))
        except (urllib.error.URLError, OSError) as e:
            raise IndiaError("could not reach NSE (%s): download %s in a browser and pass --offline FILE"
                             % (getattr(e, "reason", e), NSE_EQUITY_URL))
    kept, counts = build_universe(text)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k != "rows"}
    if a.json:
        print(json.dumps({"count": len(kept), "rows": counts["rows"], "dropped": dropped,
                          "out": None if a.no_write else a.out}, indent=2))
    else:
        print("NSE equity list: %s rows. Kept %s stocks in series EQ."
              % ("{:,}".format(counts["rows"]), "{:,}".format(len(kept))))
        if dropped:
            print("Dropped: " + ", ".join("%s %s" % ("{:,}".format(v), k)
                                          for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching daily closes for %s NSE symbols (%d spark requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "3mo", "1d", a.pause, a)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_volume)
    if not counts["fetched"]:
        nothing_fetched(failed, len(symbols))
    nfailed = len(symbols) - counts["fetched"]
    doc = {"created_by": "trader-india", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "exchange": "NSE", "as_of": ("%s close" % last_date.isoformat()) if last_date else None,
           "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed, "source": "yahoo",
           "filters": {"min_price_inr": a.min_price, "min_volume": a.min_volume}, "note": NOTE, "hits": hits}
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2))
        return 0 if hits else 1
    print("NSE daily scan, %s IST, closes through %s (Yahoo, daily closes only)."
          % (to_ist(now).strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (Rs %g+ and %s+ shares)%s; %s scanned."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
             (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
             "{:,}".format(counts["fetched"] - counts["illiquid"]), a.min_price, "{:,}".format(a.min_volume),
             (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
             "{:,}".format(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-12s %14s  RSI %5.1f  %s" % (h["symbol"], rupees(h["price"]), h["rsi"],
                                                ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s." % a.out)
    print(NOTE + " Next: pick one chart, then `india.py entry SYMBOL` after 9:30 AM IST.")
    return 0 if hits else 1


def cmd_orb(a):
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg["no_entries_after"], "--until")
    explicit_day = None
    offline_syms = None
    data = failed = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise IndiaError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    confirm = None
    if not a.offline:
        today = to_ist(parse_now(None)).date()
        if (today - day).days > 6:
            raise IndiaError("Yahoo's free 5-minute history is short: replay %s with --offline" % day.isoformat())
        rng = "1d" if day == today else "5d"
        progress(a, "Stage 1: 5-minute closes for %s NSE symbols (%d spark requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, rng, "%dm" % BAR_MIN, a.pause, a)

        def confirm(sym):
            if a.pause:
                SLEEP(a.pause)
            return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN)), sym)
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume,
                                     confirm, a.max_confirm)
    failed = dict(failed or {})
    failed.update(failed2)
    if not counts["fetched"]:
        nothing_fetched(failed, len(symbols))
    nfailed = len([s for s in symbols if s in failed])
    doc = {"created_by": "trader-india", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "exchange": "NSE", "as_of": "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M")),
           "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed,
           "unconfirmed": counts["unconfirmed"], "source": "yahoo", "or_minutes": a.or_minutes,
           "interval": "%dm" % BAR_MIN, "note": NOTE, "hits": hits}
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2))
        return 0 if hits else 1
    note = calendar_note(day)
    if note:
        print(note)
    print("NSE opening-range breakout scan, %s %s IST: %d-minute range (9:15 to %s), 5-minute bars, Yahoo."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %s of %s charts%s; %s usable; %s candidates, %s confirmed with real highs and lows."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
             (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
             "{:,}".format(counts["scanned"]), "{:,}".format(counts["candidates"]),
             "{:,}".format(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded; --max-confirm 0 checks all)."
              % ("{:,}".format(counts["unconfirmed"]), "{:,}".format(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), "{:,}".format(counts["confirmed"] if counts["unconfirmed"] else counts["scanned"]),
                 clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %8s  %-12s %14s  range %s to %s  %+.2f%% over"
                  % (h["at"], h["symbol"], rupees(h["price"]), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s." % a.out)
    print(NOTE + " Next: pick one chart, then `india.py entry SYMBOL`.")
    return 0 if hits else 1


def cmd_entry(a):
    now = parse_now(a.now)
    cfg = read_config(a.config)
    sym = from_yahoo(a.symbol)
    until = parse_hhmm(cfg["no_entries_after"], "no_entries_after")
    series = load_one(sym, a.offline, "1d")
    res = entry_status(series, now, a.or_minutes, until, a.level)
    res.update(symbol=sym, exchange="NSE", setup="orb" if a.level is None else "level",
               created_by="trader-india", checked_at=to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    if not a.no_write and res["status"] == "triggered":
        write_json(a.out, res)
    if a.json:
        print(json.dumps(res, indent=2))
        return 0 if res["status"] == "triggered" else 1
    st = res["status"]
    rng = res.get("opening_range")
    if rng:
        print("%s on NSE: opening range %s to %s (the first %d minutes)." % (sym, _p(rng["low"]), _p(rng["high"]),
                                                                            a.or_minutes))
    if st == "triggered":
        print("TRIGGERED at %s: the bar closed at %s, %+.2f%% over the line." % (res["entry_time"], _p(res["entry"]),
                                                                                res["pct_over"]))
        if not a.no_write:
            print("Wrote %s. Next: `india.py plan`." % a.out)
    elif st == "waiting":
        print("WAITING: last close %s (the %s bar), %.2f%% under the line at %s." % (_p(res["last"]), res["last_bar"],
                                                                                     res["away_pct"], _p(res["line"])))
    else:
        print("%s: %s." % (st.upper().replace("_", " "), res.get("why")))
    print("Yahoo's 5-minute bars, which can lag. It never places an order.")
    return 0 if st == "triggered" else 1


def cmd_plan(a):
    now = parse_now(a.now)
    cfg = read_config(a.config)
    src = None
    if a.entry is not None:
        if not a.symbol:
            raise IndiaError("--entry needs --symbol")
        e = {"symbol": from_yahoo(a.symbol), "entry": a.entry, "date": to_ist(now).date().isoformat(),
             "setup": "manual", "opening_range": None}
        if a.or_high is not None and a.or_low is not None:
            e["opening_range"] = {"high": a.or_high, "low": a.or_low}
    else:
        e = read_json(a.entry_file, "entry")
        src = a.entry_file
        if e.get("status") != "triggered":
            raise IndiaError("%s is not a triggered entry" % a.entry_file)
    rng = e.get("opening_range") or {}
    srule, sparam = parse_stop(a.stop)
    trule, tparam = parse_target(a.target)
    p = compute_plan(e["entry"], srule, sparam, trule, tparam, float(cfg["tick_size"]), rng.get("high"), rng.get("low"))
    plan = dict(p, created_by="trader-india", exchange="NSE", symbol=e["symbol"], side="buy", date=e.get("date"),
                setup=e.get("setup"), entry_time=a.entry_time or e.get("entry_time"), entry_bar=e.get("entry_bar"),
                stop_rule=srule, opening_range=e.get("opening_range"),
                flat_by=a.flat_by or cfg["flat_by"], tick_size=float(cfg["tick_size"]))
    parse_hhmm(plan["flat_by"], "--flat-by")
    if not a.no_write:
        write_json(a.out, plan)
    if a.json:
        print(json.dumps(plan, indent=2))
        return 0
    print("%s buy on NSE, entry %s%s." % (plan["symbol"], _p(plan["entry"]),
                                        (" at %s" % plan["entry_time"]) if plan.get("entry_time") else ""))
    print("Stop %s (%s), %s a share at risk. Target %s (%s). Reward:risk %.2f before charges."
          % (_p(plan["stop"]), plan["stop_label"], _p(plan["risk_per_share"]), _p(plan["target"]),
             plan["target_label"], plan["reward_risk"]))
    print("Flat by %s IST either way: most brokers square off intraday positions themselves around 3:20 PM."
          % plan["flat_by"])
    if not a.no_write:
        print("Wrote %s%s. Next: `india.py size`." % (a.out, (" from %s" % src) if src else ""))
    return 0


def cmd_replay(a):
    now = parse_now(a.now)
    plan = read_json(a.plan, "plan")
    if a.entry_time:
        plan["entry_time"], plan["entry_bar"] = a.entry_time, None
    sym = plan["symbol"]
    series = load_one(sym, a.offline, "60d")
    doc = time_machine(series, plan, now, a.replays, a.seed, a.window, a.until)
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0
    mc, h = doc["mc"], doc["history"]
    until = clock_label(parse_hhmm(mc["until"]))
    print("%s buy on NSE, %s: entry %s at %s, stop %s, target %s (%gR, risk %s a share)."
          % (sym, plan["date"], _p(plan["entry"]), plan["entry_time"], _p(plan["stop"]), _p(plan["target"]),
             mc["r_at_target"], _p(plan["entry"] - plan["stop"])))
    print("%s replays of the rest of the session to %s (%d five-minute bars), seed %d."
          % ("{:,}".format(mc["replays"]), until, mc["bars_left"], mc["seed"]))
    print("Each bar is drawn whole from %s's own 5-minute bars at the same time of day (+-%d bars) over its last "
          "%d full sessions (%s to %s)." % (sym, mc["window"], mc["pool_sessions"], mc["pool_from"], mc["pool_to"]))
    print("")
    print("  target first   %5.1f %%" % (100 * mc["p_target"]))
    print("  stop first     %5.1f %%" % (100 * mc["p_stop"]))
    print("  neither        %5.1f %%   still open at %s" % (100 * mc["p_neither"], until))
    print("  green          %5.1f %%   target first, or above the entry at %s" % (100 * mc["p_green"], until))
    print("")
    print("  Price at %s: median %s, 90 %% of replays between %s and %s."
          % (until, _p(mc["median_close"]), _p(mc["p5_close"]), _p(mc["p95_close"])))
    print("  Average R: %+.3f (R = the %s risk: %+gR at the target, -1R at the stop; before charges)."
          % (mc["avg_R"], _p(plan["entry"] - plan["stop"]), mc["r_at_target"]))
    if h:
        print("")
        print("History: the same rule on each of those %d real sessions fired %d times: %d target, %d stop, "
              "%d neither.%s" % (h["sessions"], h["triggered"], h["target"], h["stop"], h["close"],
                                 (" Average R %+.3f." % h["avg_R"]) if h["avg_R"] is not None else ""))
    print("")
    print(doc["verdict"])
    if mc["p_neither"] > 0.4:
        print("%.1f %% of the replays hit neither by %s; the price then decides those." % (100 * mc["p_neither"], until))
    print("")
    print(doc["note"])
    if not a.no_write:
        print("Wrote %s. Next: `india.py size`." % a.out)
    return 0


def cmd_replay_all(a):
    """The Time Machine on every opening-range breakout in the watchlist, one file each."""
    now = parse_now(a.now)
    cfg = read_config(a.config)
    wl = read_json(a.watchlist, "watchlist")
    if wl.get("mode") != "orb":
        raise IndiaError("replay-all needs an opening-range scan (india.py orb): a daily scan has no entry levels yet")
    date = str(wl.get("as_of") or "")[:10]
    hits = [h for h in wl.get("hits") or [] if h.get("or_high") is not None]
    if a.limit:
        hits = hits[:a.limit]
    if not hits:
        raise IndiaError("the scan in %s has no breakouts to replay" % a.watchlist)
    tick = float(cfg["tick_size"])
    trule, tparam = parse_target(a.target)
    done, failed = [], {}
    for k, h in enumerate(hits):
        sym = h["symbol"]
        try:
            p = compute_plan(h["price"], "or-mid", None, trule, tparam, tick, h["or_high"], h["or_low"])
            plan = dict(p, symbol=sym, date=date, setup="orb", stop_rule="or-mid", entry_time=h.get("at"),
                        entry_bar=h.get("bar_index"), flat_by=cfg["flat_by"], tick_size=tick,
                        opening_range={"high": h["or_high"], "low": h["or_low"],
                                       "minutes": int(wl.get("or_minutes") or 15)})
            if k and not a.offline and a.pause:
                SLEEP(a.pause)
            series = load_one(sym, a.offline, "60d")
            doc = time_machine(series, plan, now, a.replays, a.seed, 2, None)
        except IndiaError as e:
            failed[sym] = str(e)
            continue
        write_json(os.path.join(a.out_dir, sym + ".json"), doc)
        done.append((sym, doc))
    if a.json:
        print(json.dumps({"done": [s for s, _ in done], "failed": failed}, indent=2))
        return 0 if done else 1
    print("Time Machine on %d of %d breakouts from the %s scan (%s replays each, seed %d):"
          % (len(done), len(hits), date, "{:,}".format(a.replays), a.seed))
    for sym, doc in done:
        mc = doc["mc"]
        print("  %-12s target first %5.1f %%  stop first %5.1f %%  neither %5.1f %%  %s"
              % (sym, 100 * mc["p_target"], 100 * mc["p_stop"], 100 * mc["p_neither"], doc["verdict"].split(":")[0]))
    for sym, why in failed.items():
        print("  %-12s not run: %s" % (sym, why))
    print("Listed in the scan's order, not ranked. Simulations, not predictions. Wrote %s/. "
          "Next: the dashboard shows them in its options table." % a.out_dir)
    return 0 if done else 1


def cmd_size(a):
    cfg = read_config(a.config)
    plan = read_json(a.plan, "plan")
    order, limits, why = size_order(plan, cfg)
    if a.json:
        print(json.dumps({"order": order, "limits": [{"limit": n, "how": h, "qty": q} for n, h, q in limits],
                          "refused": why or None}, indent=2))
    else:
        print("%s buy on NSE, entry %s, stop %s, target %s: %s a share at risk."
              % (plan["symbol"], _p(plan["entry"]), _p(plan["stop"]), _p(plan["target"]),
                 _p(plan["entry"] - plan["stop"])))
        print("Limits (whole shares; the smallest wins):")
        for n, h, q in limits:
            print("  %-13s %-72s %d share%s%s" % (n, h, q, "" if q == 1 else "s",
                                                  "  <- binds" if order and n == order["binds"] else ""))
    if not order:
        if not a.json:
            print("REFUSED: " + why)
        return 1
    if not a.no_write:
        write_json(a.out, {"created_by": "trader-india", "orders": [order]})
    if not a.json:
        print("Size: %d share%s of %s = %s. Estimated charges %s (brokerage plus taxes, an estimate)."
              % (order["qty"], "" if order["qty"] == 1 else "s", order["symbol"], rupees(order["value_inr"]),
                 rupees(order["est_charges_inr"])))
        print("Max loss at the stop: %s (%s on the shares plus the charges). A stop is a plan, not a promise: "
              "a gap can fill below it." % (rupees(order["max_loss_inr"]), rupees(order["loss_at_stop_inr"])))
        if not a.no_write:
            print("Wrote %s. Nothing has been placed. Next: `india.py check`." % a.out)
    return 0


def cmd_check(a):
    now = parse_now(a.now)
    cfg = read_config(a.config)
    doc = read_json(a.orders, "orders")
    log = read_json(a.trades, "trades", required=False)
    out, worst = [], 0
    for o in doc.get("orders", []):
        verdict, reasons, notes = check_order(o, cfg, log, now)
        out.append({"symbol": o.get("symbol"), "fingerprint": fingerprint(o), "verdict": verdict,
                    "reasons": reasons, "notes": notes})
        worst = max(worst, 1 if verdict == "BLOCK" else 0)
    if not out:
        raise IndiaError("%s has no orders" % a.orders)
    rec = {"date": to_ist(now).date().isoformat(), "checked_at": to_ist(now).strftime("%Y-%m-%d %H:%M IST"),
           "results": out}
    if not a.no_write:
        write_json(a.state, rec)
    if a.json:
        print(json.dumps(rec, indent=2))
        return worst
    for r, o in zip(out, doc["orders"]):
        print("%s: buy %d %s at about %s, stop %s, target %s"
              % (r["verdict"], o["qty"], o["symbol"], _p(o["entry"]), _p(o["stop"]) if o.get("stop") else "none",
                 _p(o["target"]) if o.get("target") else "none"))
        for why in r["reasons"]:
            print("  BLOCK  " + why)
        for n in r["notes"]:
            print("  note   " + n)
    print("A PASS is a rules check, not a recommendation.%s"
          % (" Next: `india.py ticket show`." if worst == 0 else ""))
    return worst


def _ticket_lines(o, cfg, ready):
    paper = bool(cfg.get("paper", True))
    head = "%s%s" % ("PAPER TRADE (practice: write it down, do not place it). " if paper else "",
                     "READY: risk check passed today." if ready else "NOT READY: run `india.py check` first.")
    return [head,
            "  Exchange NSE, BUY %d %s, market order (or a limit at %s)." % (o["qty"], o["symbol"], _p(o["entry"])),
            "  Stop-loss: a SELL %d %s stop order, trigger %s." % (o["qty"], o["symbol"], _p(o["stop"])),
            "  Target:    a SELL %d %s limit order at %s." % (o["qty"], o["symbol"], _p(o["target"])),
            "  If your app offers a bracket, cover or GTT order, those three can go in as one.",
            "  Flat by %s IST: sell whatever is still open." % (o.get("flat_by") or cfg["flat_by"]),
            "  Value %s. Max loss at the stop with charges %s. If the target fills, about %s after charges."
            % (rupees(o["value_inr"]), rupees(o["max_loss_inr"]), rupees(o["gain_at_target_inr"]))]


def cmd_ticket(a):
    now = parse_now(a.now)
    cfg = read_config(a.config)
    day = to_ist(now).date().isoformat()
    log = read_json(a.trades, "trades", required=False) or {"trades": []}
    if a.action == "result":
        if not a.symbol or a.pnl is None:
            raise IndiaError("ticket result wants SYMBOL and --pnl RUPEES (negative for a loss)")
        sym = from_yahoo(a.symbol)
        open_ = [t for t in log["trades"] if str(t.get("symbol")).upper() == sym and t.get("pnl_inr") is None]
        if not open_:
            raise IndiaError("no open trade in %s for %s" % (a.trades, sym))
        open_[-1]["pnl_inr"] = round(float(a.pnl), 2)
        open_[-1]["closed_at"] = to_ist(now).strftime("%Y-%m-%d %H:%M IST")
        write_json(a.trades, log)
        total = realized_total(log)
        print("Logged %s: %s. Running total: %s." % (sym, rupees(float(a.pnl)), rupees(total)))
        limit = float(cfg.get("total_loss_limit_inr") or 0)
        if limit and -total >= limit:
            print("You have reached your %s total loss limit. `india.py check` now blocks new entries. "
                  "Stop and review before trading again." % rupees(limit))
        return 0
    doc = read_json(a.orders, "orders")
    state = read_json(a.state, "risk", required=False) or {}
    passed = {r["fingerprint"] for r in state.get("results", []) if r.get("verdict") == "PASS"} \
        if state.get("date") == day else set()
    orders = doc.get("orders", [])
    if a.action == "show":
        for i, o in enumerate(orders, 1):
            print("ORDER %d of %d" % (i, len(orders)))
            for ln in _ticket_lines(o, cfg, fingerprint(o) in passed):
                print(ln)
        print("This tool never places an order. You type it into your broker's app yourself.")
        return 0 if orders and all(fingerprint(o) in passed for o in orders) else 1
    # placed
    if not a.yes:
        raise IndiaError("ticket placed records that YOU placed the order: add --yes once you have")
    done = []
    for o in orders:
        fp = fingerprint(o)
        if fp not in passed:
            raise IndiaError("%s did not pass `india.py check` today: not logging it" % o["symbol"])
        if any(t.get("fingerprint") == fp for t in log["trades"]):
            continue
        log["trades"].append({"date": day, "symbol": o["symbol"], "qty": o["qty"], "entry": o["entry"],
                              "stop": o["stop"], "target": o["target"], "max_loss_inr": o["max_loss_inr"],
                              "paper": bool(cfg.get("paper", True)), "fingerprint": fp, "pnl_inr": None,
                              "placed_at": to_ist(now).strftime("%Y-%m-%d %H:%M IST")})
        done.append(o["symbol"])
    write_json(a.trades, log)
    print("Logged %s for today's limits.%s" % (", ".join(done) or "nothing new",
                                             " When it ends: `india.py ticket result SYMBOL --pnl RUPEES`."
                                             if done else ""))
    return 0


def cmd_status(a):
    now = parse_now(a.now)
    cfg = read_config(a.config)
    log = read_json(a.trades, "trades", required=False) or {"trades": []}
    day = to_ist(now).date()
    today = trades_today(log, day)
    used = sum(float(t.get("max_loss_inr") or 0) for t in today)
    total = realized_total(log)
    limit = float(cfg.get("total_loss_limit_inr") or 0)
    ok, why = trading_day(day)
    print("%s IST. NSE %s." % (to_ist(now).strftime("%a %Y-%m-%d %H:%M"),
                               "trades 9:15 AM to 3:30 PM today" if ok else "is closed today (%s)" % why))
    print("Today: %d of %d trades, %s of %s daily risk used."
          % (len(today), int(cfg["max_trades_per_day"]), rupees(used), rupees(float(cfg["max_daily_loss_inr"]))))
    for t in today:
        print("  %s %d at %s, stop %s, %s" % (t["symbol"], t["qty"], _p(t["entry"]), _p(t["stop"]),
                                             "open" if t.get("pnl_inr") is None else rupees(t["pnl_inr"])))
    if limit:
        print("Running total of closed trades: %s; total loss limit %s (%s left)."
              % (rupees(total), rupees(limit), rupees(max(0.0, limit + total))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The India Desk (trader-india): one NSE day trade, in rupees.")
    sub = ap.add_subparsers(dest="cmd")

    def base(p):
        p.add_argument("--now", help='pretend it is this time ("2026-10-29 10:15" India time, or ...Z)')
        p.add_argument("--config", default=path("config.json"))
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("universe", help="NSE's equity list -> trader/india/universe.txt")
    p.add_argument("--out", default=path("universe.txt"))
    p.add_argument("--offline", metavar="FILE", help="a saved EQUITY_L.csv")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    def scan(p, min_price, min_volume):
        base(p)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE")
        p.add_argument("--universe")
        p.add_argument("--limit", type=int)
        p.add_argument("--min-price", type=float, default=min_price, help="rupees")
        p.add_argument("--min-volume", type=int, default=min_volume)
        p.add_argument("--offline", metavar="PATH")
        p.add_argument("--out", default=path("watchlist.json"))
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE)
        p.add_argument("--show", type=int, default=50)
        p.add_argument("--quiet", action="store_true")

    scan(sub.add_parser("daily", help="pre-market setups from daily closes"), 50.0, 100000)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    scan(p, 50.0, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this India time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after)")

    p = sub.add_parser("entry", help="one stock, bar by bar: waiting / triggered / expired")
    base(p)
    p.add_argument("symbol")
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--level", type=float, help="trigger on a close above this price instead of the range high")
    p.add_argument("--offline", metavar="PATH")
    p.add_argument("--out", default=path("entry.json"))
    p.add_argument("--no-write", action="store_true")

    p = sub.add_parser("plan", help="stop and target -> trader/india/plan.json")
    base(p)
    p.add_argument("--entry-file", default=path("entry.json"))
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="your own entry price (with --symbol)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--stop", default="or-mid")
    p.add_argument("--target", default="2R")
    p.add_argument("--flat-by")
    p.add_argument("--entry-time", help="when the entry bar closed, like 10:00 AM (for --entry plans)")
    p.add_argument("--out", default=path("plan.json"))
    p.add_argument("--no-write", action="store_true")

    p = sub.add_parser("replay", help="the Time Machine: 10,000 replays of the rest of the session")
    base(p)
    p.add_argument("--plan", default=path("plan.json"))
    p.add_argument("--offline", metavar="PATH", help="a saved 60-day 5-minute chart")
    p.add_argument("--entry-time", help="override the plan's entry time, like 9:40 AM")
    p.add_argument("--until", help="end each replay at HH:MM (default: the plan's flat_by)")
    p.add_argument("--replays", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--window", type=int, default=2)
    p.add_argument("--out", default=path("timemachine.json"))
    p.add_argument("--no-write", action="store_true")

    p = sub.add_parser("replay-all", help="the Time Machine on every breakout in the scan")
    base(p)
    p.add_argument("--watchlist", default=path("watchlist.json"))
    p.add_argument("--offline", metavar="PATH", help="a folder of <SYMBOL>.json 60-day charts, or one bundle file")
    p.add_argument("--target", default="2R")
    p.add_argument("--limit", type=int, help="only the first N breakouts")
    p.add_argument("--replays", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--pause", type=float, default=PAUSE)
    p.add_argument("--out-dir", default=path("replays"))

    p = sub.add_parser("size", help="shares for your rupee limits -> trader/india/orders.json")
    base(p)
    p.add_argument("--plan", default=path("plan.json"))
    p.add_argument("--out", default=path("orders.json"))
    p.add_argument("--no-write", action="store_true")

    p = sub.add_parser("check", help="PASS / BLOCK against your rules")
    base(p)
    p.add_argument("--orders", default=path("orders.json"))
    p.add_argument("--trades", default=path("state/trades.json"))
    p.add_argument("--state", default=path("state/risk.json"))
    p.add_argument("--no-write", action="store_true")

    p = sub.add_parser("ticket", help="show the order, log that you placed it, log how it ended")
    base(p)
    p.add_argument("action", choices=["show", "placed", "result"])
    p.add_argument("symbol", nargs="?")
    p.add_argument("--yes", action="store_true", help="with placed: you placed this exact order yourself")
    p.add_argument("--pnl", type=float, help="with result: rupees, negative for a loss, after charges")
    p.add_argument("--orders", default=path("orders.json"))
    p.add_argument("--trades", default=path("state/trades.json"))
    p.add_argument("--state", default=path("state/risk.json"))

    p = sub.add_parser("status", help="today's trades and the running total")
    base(p)
    p.add_argument("--trades", default=path("state/trades.json"))

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb, "entry": cmd_entry,
                "plan": cmd_plan, "replay": cmd_replay, "replay-all": cmd_replay_all, "size": cmd_size, "check": cmd_check, "ticket": cmd_ticket,
                "status": cmd_status}[a.cmd](a)
    except IndiaError as e:
        print("india.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("india.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
