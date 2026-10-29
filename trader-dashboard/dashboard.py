#!/usr/bin/env python3
"""Trade Dashboard for trader-dashboard: one page, US and India. Standard library only, no network.

    python3 dashboard.py                      # -> trader/dashboard.html
    python3 dashboard.py --now "2026-10-19 10:16" --market us
    python3 dashboard.py --note-us "Replay of Oct 19 2026" --note-india "DEMO prices"

It reads what the other skills already wrote and puts each market's trade on
one card, in this order:

    Stock, Buy at, Stop-loss, Target, Close-out time, Time Machine odds,
    Shares, Charges, Max loss at the stop, Risk check, and the order to type

US files (dollars, New York time) are read from trader/:
    plan.json (trader-exit), entry.json (trader-entry), timemachine.json,
    orders.json (trader-budget), state/risk.json (trader-risk),
    state/broker.json (trader-broker), watchlist.json (trader-scanner)
India files (rupees, India time) are read from trader/india/ (trader-india).

A step that has not run shows as "not run yet" with the command that runs it.
A plan from an earlier day is marked OLD. A risk check counts only when it is
from the plan's own day and names the same stock. Nothing here places an
order or picks a stock: a card is a plan you can check, not a recommendation.
"""

import argparse
import datetime
import html
import json
import os
import sys

NOTE = ("Charts that matched a rule, and a plan you can check. Not picks: some of these trades will lose, "
        "and the stop is what keeps a loss small. Education and tooling, not financial advice.")

CSS = """
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1d1b;--muted:#68655e;--line:#e2ded5;--good:#1d7348;--good-bg:#e2f1e9;
--bad:#b0261e;--bad-bg:#f8e2df;--warn:#7d5400;--warn-bg:#faedd2;--blue:#2a56bd}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141412;--card:#1e1e1b;--ink:#ecebe6;
--muted:#a6a39a;--line:#33322d;--good:#62c592;--good-bg:#16301f;--bad:#f0897f;--bad-bg:#3a1c19;--warn:#efc061;
--warn-bg:#382c12;--blue:#90b0ff}}
:root[data-theme="dark"]{--bg:#141412;--card:#1e1e1b;--ink:#ecebe6;--muted:#a6a39a;--line:#33322d;--good:#62c592;
--good-bg:#16301f;--bad:#f0897f;--bad-bg:#3a1c19;--warn:#efc061;--warn-bg:#382c12;--blue:#90b0ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:24px;margin:0}h2{font-size:19px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px}
.box{border-radius:10px;padding:10px 14px;margin:12px 0;font-size:14px}
.warn{background:var(--warn-bg);color:var(--warn)}.bad{background:var(--bad-bg);color:var(--bad)}
.good{background:var(--good-bg);color:var(--good)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
table.trade{width:100%;border-collapse:collapse;margin-top:10px}
table.trade th{width:42%;text-align:left;font-weight:600;color:var(--muted);font-size:14px;padding:10px 8px 10px 0;
vertical-align:top;border-bottom:1px solid var(--line)}
table.trade td{padding:10px 0;border-bottom:1px solid var(--line);vertical-align:top}
table.trade tr:last-child th,table.trade tr:last-child td{border-bottom:0}
.big{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.why{display:block;color:var(--muted);font-size:13px}
.buy .big{color:var(--blue)}.stop .big{color:var(--bad)}.target .big{color:var(--good)}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:13px;font-weight:700}
.odds{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--line);margin:6px 0}
.odds i{display:block;height:100%}.ot{background:var(--good)}.os{background:var(--bad)}.on{background:var(--muted);opacity:.4}
.missing{color:var(--muted);font-size:14px}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
pre{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;white-space:pre-wrap;background:var(--bg);
border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:6px 0 0}
details{margin-top:12px}summary{cursor:pointer;font-weight:600;font-size:14px}
ul.hits{margin:8px 0 0;padding-left:18px;font-size:14px}
footer{color:var(--muted);font-size:12px;margin-top:24px}
"""


# ---------------------------------------------------------------- time

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """-4 in US daylight time (2nd Sunday of March to 1st Sunday of November), else -5."""
    start = datetime.datetime.combine(_nth_sunday(utc.year, 3, 2), datetime.time(7, 0))
    end = datetime.datetime.combine(_nth_sunday(utc.year, 11, 1), datetime.time(6, 0))
    return -4 if start <= utc.replace(tzinfo=None) < end else -5


def local_now(utc, market):
    if market == "india":
        return utc + datetime.timedelta(hours=5, minutes=30)
    return utc + datetime.timedelta(hours=eastern_offset(utc))


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as UTC here; give ...+05:30 or
    ...-04:00 to be exact. No --now means the machine's clock."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    try:
        dt = datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit('dashboard.py: --now wants a time like "2026-10-19T14:16:00Z"')
    return dt if dt.tzinfo is None else (dt - dt.utcoffset()).replace(tzinfo=None)


# ---------------------------------------------------------------- reading

def read_json(path):
    """The parsed file, or None when it is missing or not valid JSON."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_market(root, market):
    names = ["watchlist", "entry", "plan", "timemachine", "orders", "state/risk"]
    names.append("state/trades" if market == "india" else "state/broker")
    return {n.replace("state/", ""): read_json(os.path.join(root, n + ".json")) for n in names}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _rupees(x):
    neg = x < 0
    whole, frac = ("%.2f" % abs(x)).split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return "%sRs %s.%s" % ("-" if neg else "", whole, frac)


def money(x, market):
    if x is None:
        return "-"
    return _rupees(x) if market == "india" else ("-" if x < 0 else "") + "${:,.2f}".format(abs(x))


def price(x):
    return "-" if x is None else "{:,.2f}".format(x)


def card_data(d, market, today):
    """The files of one market -> the numbers on its card. Pure."""
    plan, entry, tm, orders, risk = d.get("plan"), d.get("entry"), d.get("timemachine"), d.get("orders"), d.get("risk")
    c = {"market": market, "steps": {}}
    if not plan:
        return c
    sym = str(plan.get("symbol") or "").upper()
    c.update(symbol=sym, date=plan.get("date"), old=bool(plan.get("date") and plan["date"] != today),
             entry=_f(plan.get("entry")), entry_time=plan.get("entry_time"), stop=_f(plan.get("stop")),
             stop_label=plan.get("stop_label"), target=_f(plan.get("target")), target_label=plan.get("target_label"),
             flat_by=plan.get("flat_by"))
    if c["entry"] is not None and c["stop"] is not None:
        c["risk_share"] = round(c["entry"] - c["stop"], 2)
    if entry and str(entry.get("symbol") or "").upper() == sym:
        c["signal"] = entry.get("entry_time") or entry.get("time")
    # the Time Machine
    if tm:
        tsym = str(tm.get("symbol") or (tm.get("plan") or {}).get("symbol") or "").upper()
        tdate = (tm.get("plan") or {}).get("date")
        if tsym == sym and (not tdate or tdate == c["date"]):
            mc = tm.get("mc") or {}
            h = tm.get("history") or {}
            c["odds"] = {"target": mc.get("p_target"), "stop": mc.get("p_stop"), "neither": mc.get("p_neither"),
                         "until": mc.get("until"), "replays": mc.get("replays"), "verdict": tm.get("verdict"),
                         "history": h if h.get("triggered") is not None else None}
    # the order
    olist = [o for o in (orders or {}).get("orders") or [] if str(o.get("symbol") or "").upper() == sym]
    if olist:
        o = olist[0]
        qty = _f(o.get("qty"))
        c["qty"] = int(qty) if qty is not None else None
        if market == "india":
            c.update(value=_f(o.get("value_inr")), charges=_f(o.get("est_charges_inr")),
                     max_loss=_f(o.get("max_loss_inr")), gain=_f(o.get("gain_at_target_inr")))
        else:
            c["value"] = round(qty * c["entry"], 2) if qty is not None and c["entry"] is not None else None
            c["charges"] = None
            c["max_loss"] = round(qty * c["risk_share"], 2) if qty is not None and c.get("risk_share") else None
            c["gain"] = round(qty * (c["target"] - c["entry"]), 2) if qty is not None and c["target"] else None
            c["order_stop"] = _f(o.get("stop_loss_stop_price"))
            c["order_target"] = _f(o.get("take_profit_limit_price"))
    # the risk check: same day as the plan, same stock
    if risk and risk.get("date") == c["date"]:
        rs = [r for r in risk.get("results") or [] if str(r.get("symbol") or "").upper() == sym]
        if rs:
            c["risk"] = {"verdict": rs[0].get("verdict"), "reasons": rs[0].get("reasons") or []}
    return c


# ---------------------------------------------------------------- page

def _e(x):
    return html.escape("" if x is None else str(x))


def _missing(cmd):
    return '<span class="missing">not run yet: <code>%s</code></span>' % _e(cmd)


MARKETS = {
    "us": {"title": "US stocks", "where": "NYSE / Nasdaq, dollars, New York time (ET)", "tz": "ET",
           "plan": "/trader-entry then /trader-exit", "tm": "/trader-timemachine",
           "size": "/trader-budget", "check": "/trader-risk", "scan": "/trader-scanner"},
    "india": {"title": "Indian stocks", "where": "NSE, rupees, India time (IST)", "tz": "IST",
              "plan": "india.py entry SYMBOL, then india.py plan", "tm": "india.py replay",
              "size": "india.py size", "check": "india.py check", "scan": "india.py daily or india.py orb"},
}


def _clock(t):
    """'15:55' -> '3:55 PM'; anything else as it is."""
    try:
        h, m = [int(x) for x in str(t).split(":")]
        return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")
    except (TypeError, ValueError):
        return str(t or "")


def render_card(c, d, cfg_paper, note=None):
    m = c["market"]
    meta = MARKETS[m]
    tz = meta["tz"]
    out = ['<section class="card"><h2>%s</h2><div class="sub">%s</div>' % (_e(meta["title"]), _e(meta["where"]))]
    if note:
        out.append('<div class="box warn">%s</div>' % _e(note))
    if not c.get("symbol"):
        out.append('<p class="missing">No trade planned yet. Start with the scan: <code>%s</code>, pick a stock, '
                   'then <code>%s</code>.</p>' % (_e(meta["scan"]), _e(meta["plan"])))
        out.append(_hits(d, m))
        out.append("</section>")
        return "".join(out)
    if c.get("old"):
        out.append('<div class="box warn">OLD: this plan is for %s, not today. Re-run the steps before acting on it.</div>'
                   % _e(c["date"]))
    rows = []

    def row(label, value, cls=""):
        rows.append('<tr class="%s"><th>%s</th><td>%s</td></tr>' % (cls, _e(label), value))

    row("Stock", '<span class="big">%s</span><span class="why">plan for %s</span>' % (_e(c["symbol"]), _e(c["date"])))
    sig = (" &middot; entry signal at %s %s" % (_e(c["signal"]), tz)) if c.get("signal") else ""
    row("Buy at", '<span class="big">%s</span><span class="why">about this price%s</span>' % (_e(price(c["entry"])), sig),
        "buy")
    row("Stop-loss", '<span class="big">%s</span><span class="why">sell if it falls to here (%s); %s a share at risk</span>'
        % (_e(price(c["stop"])), _e(c.get("stop_label") or "your stop"), _e(price(c.get("risk_share")))), "stop")
    row("Target", '<span class="big">%s</span><span class="why">sell when it reaches here (%s)</span>'
        % (_e(price(c["target"])), _e(c.get("target_label") or "your target")), "target")
    row("Close-out time", '<span class="big">%s %s</span><span class="why">sell whatever is still open, '
        'win or lose: no overnight hold</span>' % (_e(_clock(c.get("flat_by"))), tz))
    o = c.get("odds")
    if o and o.get("target") is not None:
        pt, ps, pn = 100 * o["target"], 100 * o["stop"], 100 * o["neither"]
        hist = o.get("history")
        hline = ("<span class=\"why\">Past days the same rule fired: %d, of which %d hit the target, %d the stop, "
                 "%d neither.</span>" % (hist["triggered"], hist["target"], hist["stop"], hist["close"])) if hist else ""
        row("Time Machine odds",
            '<div class="odds"><i class="ot" style="width:%.1f%%"></i><i class="os" style="width:%.1f%%"></i>'
            '<i class="on" style="width:%.1f%%"></i></div><b>%.1f%%</b> target first &middot; <b>%.1f%%</b> stop first '
            '&middot; <b>%.1f%%</b> neither by %s<span class="why">%s</span>%s'
            '<span class="why">%s replays from the stock\'s own recent days. A simulation, not a prediction.</span>'
            % (pt, ps, pn, pt, ps, pn, _e(_clock(o.get("until"))), _e(o.get("verdict")), hline,
               _e("{:,}".format(o.get("replays") or 0))))
    else:
        row("Time Machine odds", _missing(meta["tm"]))
    if c.get("qty") is not None:
        row("Shares", '<span class="big">%d</span><span class="why">worth about %s</span>'
            % (c["qty"], _e(money(c.get("value"), m))))
        if m == "india":
            row("Charges", '<span class="big">%s</span><span class="why">estimated brokerage and taxes for the buy and '
                'the sell; your contract note has the real figure</span>' % _e(money(c.get("charges"), m)))
        else:
            row("Charges", '<span class="big">not estimated</span><span class="why">add your broker\'s fees; buying US '
                'stocks from India also costs currency conversion both ways</span>')
        row("Max loss at the stop", '<span class="big">%s</span><span class="why">if the stop fills at its price%s; '
            'a gap can fill worse</span>' % (_e(money(c.get("max_loss"), m)), ", charges included" if m == "india" else ""))
        if c.get("gain") is not None:
            row("If the target fills", '<span class="big">%s</span>' % _e(money(c["gain"], m)))
    else:
        row("Shares", _missing(meta["size"]))
        row("Charges", _missing(meta["size"]))
    r = c.get("risk")
    if r:
        if r["verdict"] == "PASS":
            v = '<span class="pill good">PASS</span><span class="why">every one of your limits allows this trade. ' \
                'A rules check, not a recommendation.</span>'
            if o and "toward the stop" in str(o.get("verdict") or ""):
                v += '<div class="box bad">The Time Machine leans toward the stop for this plan: PASS only means ' \
                     'your limits allow it, not that the odds favour it.</div>'
        else:
            v = '<span class="pill bad">BLOCK</span>' + "".join('<span class="why">%s</span>' % _e(x) for x in r["reasons"])
        row("Risk check", v)
    else:
        row("Risk check", _missing(meta["check"]))
    out.append('<table class="trade">%s</table>' % "".join(rows))
    if c.get("qty") is not None and r and r["verdict"] == "PASS":
        paper = "PAPER TRADE: practise first, write it down rather than placing it.\n" if cfg_paper else ""
        ex = "NSE " if m == "india" else ""
        out.append('<details open><summary>The order to type into your broker\'s app</summary><pre>%s</pre></details>'
                   % _e("%s1. BUY %d %s%s at market (or a limit at %s)\n2. Stop-loss: SELL %d at a stop of %s\n"
                        "3. Target: SELL %d at a limit of %s\n4. At %s %s: sell whatever is still open\n"
                        "Nothing here places an order. You type it yourself."
                        % (paper, c["qty"], ex, c["symbol"], price(c["entry"]), c["qty"], price(c["stop"]),
                           c["qty"], price(c["target"]), _clock(c.get("flat_by")), tz)))
    out.append(_hits(d, m))
    out.append("</section>")
    return "".join(out)


def _hits(d, m):
    wl = d.get("watchlist")
    if not wl or not wl.get("hits"):
        return ""
    hits = wl["hits"]
    items = []
    for h in hits[:12]:
        bits = [price(_f(h.get("price")))]
        if h.get("at"):
            bits.append("broke out at %s" % h["at"])
        if h.get("setups") and h["setups"] != ["orb"]:
            bits.append(", ".join(str(s).replace("_", " ") for s in h["setups"]))
        items.append("<li><b>%s</b> %s</li>" % (_e(h.get("symbol")), " &middot; ".join(_e(x) for x in bits)))
    more = ('<li class="missing">and %d more</li>' % (len(hits) - 12)) if len(hits) > 12 else ""
    return ('<details><summary>The scan: %d chart%s matched a rule (as of %s)</summary><ul class="hits">%s%s</ul>'
            '<p class="sub">A list of charts that match a rule, not a list of stocks to buy. You pick; the card above '
            'is filled in for the one you pick.</p></details>'
            % (len(hits), "" if len(hits) == 1 else "s", _e(wl.get("as_of")), "".join(items), more))


def render(now_utc, markets, paper=True, notes=None):
    """markets = [(market, files dict)]. Pure: the page as a string."""
    notes = notes or {}
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trade Dashboard</title>'
           '<style>%s</style></head><body><main>' % CSS,
           '<h1>Trade Dashboard</h1><div class="sub">Made %s UTC from the files the trading skills wrote</div>'
           % _e(now_utc.strftime("%a %d %b %Y, %H:%M")),
           '<div class="box warn">%s</div>' % _e(NOTE)]
    for m, d in markets:
        today = local_now(now_utc, m).date().isoformat()
        out.append(render_card(card_data(d, m, today), d, paper, notes.get(m)))
    out.append('<footer>Prices from Yahoo Finance (free, can lag): check them in your broker\'s app before acting. '
               'Most people who day trade lose money. Education and tooling, not financial advice.</footer>'
               '</main></body></html>')
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trade Dashboard (trader-dashboard): one page, US and India.")
    ap.add_argument("--market", choices=["both", "us", "india"], default="both")
    ap.add_argument("--us-dir", default="trader")
    ap.add_argument("--india-dir", default=os.path.join("trader", "india"))
    ap.add_argument("--out", default=os.path.join("trader", "dashboard.html"))
    ap.add_argument("--now", help='pretend it is this time, like "2026-10-19T14:16:00Z"')
    ap.add_argument("--note-us", help="a line shown on the US card, like a replay date")
    ap.add_argument("--note-india", help="a line shown on the India card")
    a = ap.parse_args(argv)
    now = parse_now(a.now)
    markets = []
    if a.market in ("both", "us"):
        markets.append(("us", load_market(a.us_dir, "us")))
    if a.market in ("both", "india"):
        markets.append(("india", load_market(a.india_dir, "india")))
    cfg = read_json(os.path.join(a.india_dir if a.market == "india" else a.us_dir, "config.json")) or {}
    page = render(now, markets, bool(cfg.get("paper", True)), {"us": a.note_us, "india": a.note_india})
    d = os.path.dirname(a.out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("Wrote %s. Open it in your browser." % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
