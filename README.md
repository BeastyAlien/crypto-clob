# crypto-clob — cross-venue BTC order-flow engine

A research tool that consumes **public, unauthenticated** order-book and trade
streams from up to seven crypto exchanges and fuses them into a single
consolidated view of BTC-USD liquidity and order flow.

> **Read-only.** It subscribes to public market data only and never places,
> modifies, or cancels orders. Nothing here is financial advice or a
> profitability guarantee — treat every metric as a heuristic on public data.

## Venues

| Venue | Feed | Book depth | L3-ish? |
|---|---|---|---|
| Binance | `depth` diff + `aggTrade` | 5,000 levels | aggregated L2 |
| Bybit (linear perp) | `orderbook.500` + `publicTrade` | 500 | aggregated L2 |
| OKX | `books` + `trades` | 400 | aggregated L2 |
| Kraken | `book:1000` + `trade` | 1,000 | aggregated L2 |
| Coinbase Advanced | `level2` + `market_trades` | 1,000 | aggregated L2 |
| Bitstamp | `diff_order_book` + `live_trades` | full | aggregated L2 |
| Bitfinex | `book tBTCUSD R0` + `trades` | per-order | **raw per-order** |

All symbols are mapped to the unified **BTC-USD** index; prices are bucketed to
a uniform tick (default $1.00) so venue-specific tick sizes don't break
aggregation.

## Metrics

- **Global consolidated book** — per-bucket volume totals with per-venue
  composition (liquidity heatmap), best bid/ask across all venues.
- **NOBI** — normalized order-book imbalance over N levels around the global touch.
- **OFI** — touch-region order-flow imbalance: per-venue bid-volume refresh minus
  ask-volume refresh in the top-N window, accumulated tick by tick.
- **CVD** — cumulative volume delta (aggressive buyer-initiated minus
  seller-initiated volume), per venue and global.
- **Aggressiveness ratio** — buyer-initiated share of trade volume in a rolling window.
- **Market sweeps** — burst detection: same-side trades on ≥ K venues within a
  narrow millisecond window.
- **Iceberg flags** — executed volume vs displayed depth at a level exceeding a
  ratio within a lookback → hidden-order heuristic.
- **Spoofing / layering flags** — large orders (notional, distance-from-mid
  thresholds) placed and cancelled within a short lifetime without a fill.
- **Absorption** — high in-window CVD with a contained mid move.
- **Lead-lag** — which venue moves its mid first within a window (price
  discovery heuristic).
- **Spread divergence** — cross-venue spread z-score alerts.

## Sync protocol

Diff-based venues (Binance/Bybit/OKX) follow the standard pattern: while a
REST snapshot is in flight, diffs are buffered; they are replayed after the
snapshot anchors the book, with sequence-rule verification (Binance `lastUpdateId`
continuity, Bybit/OKX `seqId` +1 continuity). Sequence gaps trigger a fresh
REST snapshot. Kraken/Coinbase/Bitstamp/Bitfinex push snapshots on subscribe
(nothing to anchor). Timestamps: exchange message time where available,
otherwise receive-time `time.time_ns()` — all aligned to UTC epoch ms.

## Install & run

```bat
python -m pip install -r requirements.txt
python run.py --duration 120
python run.py --exchanges binance bitfinex --duration 0
```

Outputs under `data/`:
- `metrics_<utc>.jsonl` — one aggregated row per summary interval.
- `events_<utc>.jsonl` — sweep / iceberg / spoof / absorption events.

## Configuration (`config.json`)

`tick_size`, `nobi_levels`, `heatmap_rows`, sweep/iceberg/spoof/absorption
thresholds, lead-lag window, spread z-threshold, exchange list and symbols.
Defaults are tuned for BTC-USD; adjust thresholds after observing a live run.

## MT5 trading bridge (optional)

`cvd_bridge.py` turns the live CVD stream into BUY/SELL signals that the
MetaTrader 5 Expert Advisor **CvdSignalTrader.mq5** (in `MQL5\Experts`) trades:

1. Run alongside the engine/UI, in its own terminal:
   ```bat
   python cvd_bridge.py --duration 0
   ```
2. It tails the newest `metrics_*.jsonl`, smooths the CVD source and on a
   regime flip writes `cvd_signal.sig` into the MT5 `<data folder>`:
   ```
   <serial>|<UTC time>|<cvd_prev>|<cvd_now>|<BUY|SELL>
   ```
   CVD source (`bridge_config.json` -> `cvd_mode`):
   - `"window"` (default) - rolling global CVD (sum of `cvd_window_venue`);
     oscillates around zero, so flips like -4 ... +1 -> BUY happen naturally.
   - `"total"` - cumulative `cvd_total` since engine start; drifts, rarely flips.
3. Attach `CvdSignalTrader.ex5` to any chart - the EA trades the **chart
   symbol** (`_Symbol`). On a new BUY serial it opens long (flipping out of a
   short first if enabled); SELL mirrors. Magic number, volume, SL/TP,
   flip behaviour and poll interval are EA inputs.
4. Enable trading for the terminal: Tools -> Options -> Expert Advisors ->
   "Allow Expert Advisors to trade" (and "Allow Expert Advisor to have
   trading functions"). The EA logs a warning until then.

Validate on recorded data before going live:
```bat
python cvd_bridge.py --dry-run --file data\metrics_20260906_184703.jsonl
```
Not financial advice - the CVD flip is a heuristic on public order flow;
validate with the Strategy Tester and never risk capital you cannot afford.

### Chart markers

When the EA processes a signal it also draws on the chart (input switches
`InpDrawMarkers` / `InpUseAlert`):

- **BUY** -> green up arrow + `CVD BUY` label at the Ask level, signal time.
- **SELL** -> orange/red down arrow + `CVD SELL` label at the Bid level.
- `InpMaxMarkers` caps how many marker events stay on the chart (oldest
  removed); all markers use the `CVDX_` prefix and are deleted on EA deinit.

### Running 24/7 when the PC is off

The EA, the bridge and the engine all run on one machine - if that machine
is off, everything stops. There is no "cloud" MetaTrader; the standard
solution is a **Windows VPS** (a small always-on remote Windows PC):

1. Rent a Windows VPS (any provider; 2+ GB RAM, 2 CPUs, Windows Server or
   Windows 11) - roughly $10-25/month.
2. RDP into it: install MetaTrader 5 + Python, copy this project and
   `CvdSignalTrader.ex5`, and run the same three pieces there:
   `run.py --duration 0`, `cvd_bridge.py --duration 0`, MT5 + EA attached.
3. Set Windows auto-login + Task Scheduler "on startup" for MT5 and a
   start script so everything comes back after a reboot.
4. Check the VPS has a public/fixed IP and reliable uptime; keep the demo
   account logged in (demo accounts may expire after inactivity).

Alternative: keep the PC always on (power settings: never sleep; UPS for
power cuts; Task Scheduler + auto-login for reboots). Both approaches keep
the same layout - engine, bridge and terminal must stay on one machine
because the bridge writes the signal file into the terminal's `MQL5\Files`
folder.

## Caveats

- Websocket/REST availability varies by region (Binance/OKX 451 in some
  regions, Coinbase outside supported countries) — reconnect + backoff handles
  intermittent issues.
- Bitstamp's diff channel pushes the full book each frame (treated as
  snapshot); Bitfinex `R0` is maintained as per-order state internally and
  re-aggregated to the unified tick grid.
- These are pattern heuristics: an "iceberg/spoof/absorption flag" is not proof
  of intent, and past or live patterns do not guarantee future behavior.