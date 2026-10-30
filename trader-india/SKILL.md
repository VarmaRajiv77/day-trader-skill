---
name: trader-india
description: >-
  The India Desk. The day-trade chain for Indian stocks on NSE, in rupees and
  India time: builds the list of NSE equity (series EQ) stocks from NSE's own
  file, runs a pre-market scan of daily closes (20-day breakout, bull flag,
  reclaimed the 20-day average, oversold, oversold bounce) and an opening-range
  breakout scan on 5-minute bars (the 9:15 to 9:30 range), watches one stock
  for its entry, sets the stop and target to the NSE tick, replays the rest of
  the session 10,000 times from the stock's own bars (the Time Machine) plus
  every past day the rule fired, sizes the trade to
  your rupee limits with brokerage and taxes counted, checks it against your
  rules (max loss a trade, a day, and a running total loss limit), and prints
  the order to type into your broker's app (INDmoney, Zerodha, Groww, any),
  or, with INDmoney's INDstocks API switched on, sends that one order (a limit
  buy with its stop-loss and target legs) only after the user's own yes.
  Knows NSE hours and the 2026 holidays. Paper by default. Use when the user says "Indian stocks", "NSE", "BSE", "Nifty",
  "Sensex", "rupees", "INDmoney", "Zerodha", "Groww", "intraday", "scan NSE",
  "RELIANCE", "TCS", "INFY", "I have 1 lakh", "what are the odds" on an
  Indian stock, or names any NSE symbol.
---

# trader-india

The India Desk runs one day trade on NSE from scan to ticket, in rupees. It is
the same method as the US skills (scanner, entry, exit, budget, risk, broker),
plus the Time Machine, rebuilt for India: NSE's stock list, NSE's hours (9:15 AM to 3:30 PM IST, no
daylight saving) and holidays, the NSE tick, rupee limits, and Indian charges.

By default it never places an order: it ends with a ticket you type into your
broker's app yourself. With INDstocks (INDmoney's broker API) switched on, it
can send that one checked order for you, only after you say yes to it. Most
people who day trade lose money; this does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/india.py" universe                       # NSE EQUITY_L.csv -> trader/india/universe.txt
python3 "${CLAUDE_SKILL_DIR}/india.py" daily                          # before 9:15 AM IST -> trader/india/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/india.py" daily --symbols RELIANCE,TCS,INFY,HDFCBANK
python3 "${CLAUDE_SKILL_DIR}/india.py" orb                            # after 9:35 AM IST: breakouts of the 9:15 to 9:30 range
python3 "${CLAUDE_SKILL_DIR}/india.py" entry RELIANCE                 # waiting / triggered / expired -> entry.json
python3 "${CLAUDE_SKILL_DIR}/india.py" plan --stop or-mid --target 2R # -> plan.json
python3 "${CLAUDE_SKILL_DIR}/india.py" replay                         # the Time Machine: 10,000 replays -> timemachine.json
python3 "${CLAUDE_SKILL_DIR}/india.py" replay-all                     # ... on every breakout in the scan -> replays/
python3 "${CLAUDE_SKILL_DIR}/india.py" size                           # -> orders.json
python3 "${CLAUDE_SKILL_DIR}/india.py" check                          # PASS / BLOCK -> state/risk.json
python3 "${CLAUDE_SKILL_DIR}/india.py" ticket show                    # the order in plain words
python3 "${CLAUDE_SKILL_DIR}/india.py" ticket placed --yes            # only after the user says they placed it
python3 "${CLAUDE_SKILL_DIR}/india.py" ticket result RELIANCE --pnl -480
python3 "${CLAUDE_SKILL_DIR}/india.py" status                         # today's trades, the running total
```

Common flags: `--now "2026-10-29 10:15"` (India time) pins the clock,
`--offline PATH` reads saved files, `--json`, `--no-write`. Symbols are NSE's
(`RELIANCE`, `M&M`, `BAJAJ-AUTO`); the tool adds Yahoo's `.NS` itself. Exit
code 0 on a hit, a trigger or a PASS, 1 otherwise, with one plain line.

## The files

Everything lives in `trader/india/`, apart from the US files in `trader/`.

| file | written by | read by |
| --- | --- | --- |
| `config.json` | the user (copy `config.example.json`) | every step |
| `mylist.txt` | the user (copy `nifty50.txt`, the Nifty 50 as of 2026-10-29) | `--watchlist` |
| `universe.txt` | `universe` | `daily`, `orb` |
| `watchlist.json` | `daily`, `orb` | `--watchlist` |
| `entry.json` | `entry` (on a trigger) | `plan` |
| `plan.json` | `plan` | `replay`, `size` |
| `timemachine.json` | `replay` | the user (the odds, the history, the verdict) |
| `orders.json` | `size` | `check`, `ticket` |
| `state/risk.json` | `check` | `ticket` (READY only if this exact order passed today) |
| `state/trades.json` | `ticket placed`, `ticket result` | `check`, `status` |

## The config

Copy `config.example.json` to `trader/india/config.json` and set it with the
user. Keys starting with `_` are notes. The defaults are for a Rs 1,00,000
account:

- **budget_inr** 1,00,000; **cash_buffer_pct** 10; **max_position_pct** 75; **max_order_inr** 75,000.
- **max_loss_per_trade_inr** 1,000 (1 % of the budget), **max_daily_loss_inr** 2,000,
  **max_trades_per_day** 2.
- **total_loss_limit_inr** 5,000: once closed trades have lost this much in
  total, `check` blocks every new entry. Stop and review. Logged with
  `ticket result`.
- **min_reward_risk** 1.0, after charges. **require_stop** true.
- **no_entries_before** 09:30, **no_entries_after** 14:30, **flat_by** 15:10
  (brokers square off intraday positions on their own around 3:15 to 3:20 PM,
  often with a fee).
- **tick_size** 0.05. **brokerage_per_order_inr** 20 and
  **charges_pct_round_trip** 0.05 (STT, exchange fees, stamp duty, GST, as a
  percent of the buy value): estimates. The user should set them from their
  broker's charges page and check them against a contract note.
- **paper** true: tickets say PAPER TRADE. **allow_margin** and **allow_short** false.

If the user gave a budget and a loss they can take, put the loss they can take
in **total_loss_limit_inr**, not in the per-trade limit, and suggest about 1 %
of the budget a trade.

## Steps

1. Once a week: `india.py universe`. NSE sometimes refuses scripts; then the
   user downloads `EQUITY_L.csv` from nseindia.com in a browser and you run
   `universe --offline EQUITY_L.csv`.
2. Before 9:15 AM IST: `india.py daily`. Say how many charts were fetched,
   passed the floors (Rs 50 and 1,00,000 shares by default) and were scanned,
   and the top few by setup. The whole list (about 2,000 EQ stocks, 100
   requests) takes a minute or two; `--symbols` for the user's own list. A
   user with no list can start from the Nifty 50:
   `cp "${CLAUDE_SKILL_DIR}/nifty50.txt" trader/india/mylist.txt`, then
   `daily --watchlist trader/india/mylist.txt`. The Nifty 50 changes at the end
   of March and September: check niftyindices.com and edit the file.
3. After 9:35 AM IST: `india.py orb` (or `orb --watchlist trader/india/watchlist.json`).
   To see the odds of every breakout before the user picks: `india.py replay-all`
   (one request a stock), then the dashboard's options table lists them all.
4. The user picks one chart. Never pick for them. `india.py entry SYMBOL`
   until it says TRIGGERED (re-run every few minutes; it reads completed
   5-minute bars).
5. `india.py plan`: stop at the opening-range midpoint and target 2R unless
   the user asks otherwise (`--stop or-low`, `--stop 2412.50`, `--stop 1.5%`,
   `--target 1.5R`, `--target 2500`). For a stock the user found elsewhere:
   `plan --symbol TCS --entry 4120 --stop 4080 --target 4200`.
6. `india.py replay`, the Time Machine, before any money is at risk. One
   request for 60 days of the stock's 5-minute bars, then 10,000 replays of
   the rest of the session to the flat-by time (3:10 PM), each bar drawn
   from the stock's own bars at that time of day (seed 19, so anyone can
   repeat it), plus the same rule re-run on every past full session. Say the
   odds (target first, stop first, neither), the history and the verdict line
   as printed, and that it is a simulation, not a prediction. For a plan made
   with `--entry`, add `--entry-time 10:00`.
7. `india.py size`. Say which limit decided the size, the estimated charges
   and the max loss at the stop with charges. A refusal is a refusal: never
   raise a limit to fit a trade.
8. `india.py check`. PASS or BLOCK with each reason.
9. `india.py ticket show`. Read the ticket to the user. If they want to place
   it, they type it into their broker's app themselves. Only after they say
   they placed that exact order: `ticket placed --yes`. When it ends:
   `ticket result SYMBOL --pnl RUPEES` (after charges, negative for a loss).
10. With INDstocks switched on (below), `india.py broker preview`, read it to the
    user, and send only after their own yes: `india.py broker send --reply "<their words>"`.
11. `india.py status` any time: trades today, risk used, the running total.
12. After every live step, if `/trader-dashboard` is installed, run its
    `dashboard.py` so the user sees the trade on one card
    (`trader/dashboard.html`).

## Sending orders through INDstocks (INDmoney)

INDmoney's broker, INDstocks, has a trading API (docs: api-docs.indstocks.com).
`india.py broker` uses it. Off by default (`"broker": "manual"`).

The user sets it up once (`india.py broker setup` prints the steps and tests the token):

1. Generate an access token at indstocks.com/app/api-trading/access-tokens
   (valid 24 hours) and put it in the `INDSTOCKS_TOKEN` environment variable
   in their own terminal. Never ask for it, never put it in the chat or a file.
2. Register the computer's **static IP** on the same page. INDstocks refuses
   orders from any other address (an NSE rule); the IP can change once a week.
   Reading prices, positions and the order book works without it.
3. `"broker": "indstocks"` in `trader/india/config.json`. Real orders also need
   `"paper": false`, which only the user switches.

What it sends: one `POST /smart/order` for the order `check` passed today:
BUY, NSE, EQUITY, product `INTRADAY` (`indstocks_product`), a **LIMIT** at the
plan's entry (it never chases the price), a stop-loss leg (trigger at the
stop, limit `sl_limit_buffer_pct`, 0.5 %, under it) and a target leg (trigger
at the target, limit one tick above, as INDstocks requires on a BUY). The legs
wake only once the buy fills. Because the stop leg is a limit, `size` and
`check` count the loss down to the stop leg's limit, so the max loss holds.

| command | does |
| --- | --- |
| `broker setup` | prints the steps, tests the token, shows the intraday balance |
| `broker preview` | the exact order and the worst planned loss, sends nothing |
| `broker send --reply "yes"` | sends it: only for an order that passed `check` today and passes again now, only once, only with `"paper": false`, only after the user's yes |
| `broker orders` | today's trader-india orders and their status (`--all` for every order) |
| `broker positions` | open intraday positions |
| `broker cancel ORDER_ID --reply "yes"` | cancels one order or leg |
| `broker close SYMBOL --reply "yes"` | cancels the waiting stop/target legs first, then sells what is open at a limit just under the last price (use it at the flat-by time) |

Rules for Claude:

- `--reply` carries the user's own words, typed in the chat for this exact
  order after they saw the preview. Never supply a yes yourself, never reuse an
  earlier yes, never send for a trade the user has not seen.
- If a send errors or times out, never send again: run `broker orders` and
  report what INDstocks shows. The tool refuses a second send of the same order.
- After the trade ends, log it: `ticket result SYMBOL --pnl RUPEES`.

## Data and limits

- Yahoo Finance carries NSE stocks as `SYMBOL.NS`. It is free, unofficial and
  can lag or rate-limit; bars are 5-minute bars, never ticks. Check the price
  in the broker's app before acting.
- NSE holidays for 2026 are built in (16 weekday closures, read from brokers'
  copies of NSE's circular on 2026-10-29). From 2027 the tool treats every
  weekday as a trading day and says so: check NSE's circular. Muhurat trading
  is not modelled.
- Price bands: a stock locked at its upper circuit cannot be bought, and one at
  the lower circuit cannot be sold. A stop cannot help inside a circuit.
- Long only, cash only. No F&O, no margin (MTF), no short selling.
- BSE-only stocks are not covered.

## Rules

- Say what was scanned and when, in IST. A partial scan is said as partial.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never pick a stock for the user, never promise a setup works.
- Money in rupees with Indian grouping (Rs 1,00,000).
- Re-run the entry check before acting: a hit is stale once the price moves.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- By default this skill never places an order: the user types it into their broker's app. With
  `"broker": "indstocks"` and `"paper": false`, `broker send` places ONE order, only after `check`
  passed that exact order today and again at send time, and only after the user's own yes in the chat.
- The INDstocks token lives in the `INDSTOCKS_TOKEN` environment variable, never in the chat or a file.
- No F&O, margin or short selling. Intraday leverage turns a small mistake into a large loss.
- Day-trading profits in India are speculative business income for tax. Talk to a tax adviser.
