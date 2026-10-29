---
name: trader-dashboard
description: >-
  Trade Dashboard. One page for both markets, US stocks (dollars, New York
  time) and Indian stocks on NSE (rupees, India time), built from the files
  the other skills wrote. Each market gets one plain card in this order: the
  stock, buy at, stop-loss, target, close-out time, the Time Machine odds,
  shares, charges, max loss at the stop, the risk check, and the order to type
  into the broker's app, with the scan's other matches folded underneath. A
  step that has not run says so with the command that runs it; an old plan is
  marked OLD. No network, never places an order, never picks a stock. Run it
  after every live run of /trader-scanner, /trader-entry, /trader-exit,
  /trader-timemachine, /trader-budget, /trader-risk, /trader-broker or
  /trader-india, and when the user says "dashboard", "show me the trade",
  "summary", "what do I buy and when do I sell", or "give me the details".
---

# trader-dashboard

The Trade Dashboard puts the day's trade on one page, for the US and for
India, so nobody has to read nine tool outputs. It only reads files the other
skills already wrote. It fetches nothing, places nothing and picks nothing.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/dashboard.py"                  # both markets -> trader/dashboard.html
python3 "${CLAUDE_SKILL_DIR}/dashboard.py" --market india   # or --market us
python3 "${CLAUDE_SKILL_DIR}/dashboard.py" --note-us "Replay of Oct 19 2026" --note-india "DEMO prices"
```

## The card

One card a market, in this order:

| row | US, from | India, from |
| --- | --- | --- |
| Stock | `trader/plan.json` | `trader/india/plan.json` |
| Buy at, and when the signal came | `plan.json`, `entry.json` | the same in `trader/india/` |
| Stop-loss, and the risk a share | `plan.json` | `plan.json` |
| Target | `plan.json` | `plan.json` |
| Close-out time | `plan.json` `flat_by` (3:55 PM ET) | `flat_by` (3:10 PM IST) |
| Time Machine odds, verdict, history | `timemachine.json` | `timemachine.json` (`india.py replay`) |
| Shares, value | `orders.json` (trader-budget) | `orders.json` (`india.py size`) |
| Charges | not estimated: the user adds their broker's fees and the currency conversion | estimated brokerage and taxes |
| Max loss at the stop | shares x risk a share | with charges |
| Risk check | `state/risk.json` (trader-risk) | `state/risk.json` (`india.py check`) |
| The order to type | shown only after a PASS | shown only after a PASS |

Rules the page keeps:

- A Time Machine run or a risk check counts only for the same stock and the
  plan's own day; anything else shows "not run yet" with the command.
- A plan from an earlier day is marked OLD: re-run before acting.
- A PASS next to a Time Machine verdict that leans toward the stop gets a red
  note: PASS means the limits allow it, not that the odds favour it.
- Paper trading (the default) prints PAPER TRADE on the order.
- The scan's other matches are folded under the card as charts that matched a
  rule, never as picks.

## Steps

1. After any live step in either market, run `dashboard.py` and tell the user
   the path: `trader/dashboard.html`, to open in a browser.
2. Read the card back in plain words: buy at, stop-loss, target, close-out
   time, the odds as printed, shares, charges, max loss, the risk check.
3. If a row says "not run yet", offer that step next.
4. For a replay or demo data, pass `--note-us` / `--note-india` so the card
   says so at the top.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Never call a card a pick or a stock to invest in. It is a plan the user can check.
- Paper trading by default (`"paper": true`). Nothing in this skill places an order; an order goes
  through only as the broker skills describe, after the risk check and the user's own yes.
- Prices come from Yahoo Finance (free, can lag): check them in the broker's app before acting.
