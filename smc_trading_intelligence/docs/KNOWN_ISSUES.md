# Known Issues & Open Risks

Tracked from design time so they cannot be quietly forgotten. Status:
`OPEN` (no mitigation built yet) / `MITIGATED` / `ACCEPTED`.

| # | issue | impact | planned mitigation | status |
|---|---|---|---|---|
| 1 | Overlapping M5 setups are not independent samples | CIs too narrow, probability overstated | de-overlap (one open setup per symbol/direction) + block-bootstrap CI | OPEN (design) |
| 2 | Non-stationarity: SMC edge decays across regimes | backtest ≠ future | regime in similarity key, recency half-life 365 d, walk-forward only | OPEN (design) |
| 3 | Parameter sensitivity (swing_right, displacement thresholds) | edge may be a curve-fitting artefact | report sensitivity surface, not a tuned point | OPEN |
| 4 | Intrabar TP/SL sequence unknown above M1 | some labels wrong | pessimistic `SL_FIRST` default; M1 resolution when cached; ambiguity flag stored | OPEN (design) |
| 5 | Broker tick volume is not real volume; demo spreads are synthetic | volume/spread features unreliable | volume excluded from default displacement score; measured separately | OPEN (design) |
| 6 | Multiple comparisons across symbols/TFs/setup types | false positives | pre-registered hypothesis list per phase; deflated metrics | OPEN |
| 7 | Trading costs can exceed the edge on M5 gold | "profitable" backtest that loses live | costs applied at labeling time, not after | OPEN (design) |
| 8 | MT5 Python package is Windows-only | Linux/macOS users blocked from live data | CSV/Parquet path is first-class; connector is import-guarded and exits with an actionable message | MITIGATED (Phase 1) |
| 9 | MT5 terminal caps history returned per request | short backtests | chunked `copy_rates_from_pos` / `copy_rates_range` + "max bars in chart" instructions | MITIGATED (Phase 1) |
| 10 | Cold start: no historical database until enough bars are replayed | early signals get VERY_LOW reliability | correct behaviour — engine returns `NO_TRADE` / `INSUFFICIENT_SAMPLE` | ACCEPTED |
| 11 | SMC has no canonical definition; ours is one of several | results not comparable to other tools | every rule documented + config-driven; `config_hash` on every signal | ACCEPTED |
| 12 | Repo also contains an unrelated blood-bank web app | confusion | trading system fully isolated under `smc_trading_intelligence/` | ACCEPTED |
| 13 | The MT5 fetch path is untested in this environment (Linux) | a runtime error could hide in `fetch_bars` | needs one run on Windows against a real terminal; pure helpers are unit-tested | OPEN |
| 14 | Broker DST shifts (UTC+2 winter → +3 summer) mid-history | a whole season of bars off by an hour | Phase 1 stores one static offset and warns on live mismatch; a per-period offset table is needed before session features (Phase 5) | OPEN |
| 15 | Timestamps assumed to be bar OPEN time | a broker exporting close-time bars shifts everything by one bar | documented in the data contract; verify with check 3 in PHASE_1_PLAN §8 | OPEN |
| 16 | Building the full setup census scans nearest objects per bar | ~20 s for 18,539 bars; worse at scale | O(n) structure states, lazy snapshot lists and cached per-bar event maps cut it from 36 s; one `MarketContext` is reused by the backtester | ACCEPTED |
| 17 | Monte Carlo block resampling gives a far worse median than IID (-1.0R vs +7.5R on the sample) | IID figures overstate the edge | serial dependence between overlapping setups is real; the block number is the one to read | ACCEPTED |
| 18 | The Pine viewer's agreement with the Python engine is not machine-verified | the chart could disagree with the decision that matters | TradingView cannot be scripted from CI; the script says so in its header and prints no decisions or probabilities of its own | OPEN |
| 19 | `LiveBroker.place()` / `close()` / `reconcile()` have never executed | an error could hide in the MT5 order path | every gate around them is tested; the calls themselves need Windows, MT5 and a real account. Paper-trade first — that is what Phase 23 is for | OPEN |
| 20 | The Claude request path is unexercised (no key in this environment) | a wrong argument to `messages.create` would surface only on first use | the failure is caught and degrades to `local_narration()`, so the worst case is no narration, never a crashed run | ACCEPTED |
| 21 | On the sample history the engine takes **zero** trades | nothing to validate live behaviour against yet | correct behaviour, not a bug: observed rates are 0.24-0.36 with LOW reliability. It needs real, longer history before any figure means anything | ACCEPTED |
| 22 | HistData.com MT exports contain interleaved flat filler bars on duplicated, non-monotonic timestamps | the default `last` dedup keeps the filler about half the time, flattening ~0.8% of bars | `--on-duplicate widest` keeps the informative bar; documented in `_drop_duplicates`. Note the trade-off: `widest` would also keep a bad tick that a live feed later narrowed away | MITIGATED |
| 23 | HistData MT files are New York time (EST/EDT **with** DST) | ingesting a multi-month archive with one `--offset` puts a season of bars an hour out | ingest one month at a time with the right offset; verify by checking the daily 17:00 NY break lands where it should. Issue #14 (a per-period offset table) is the real fix | OPEN |
| 24 | HistData MT format reports volume as 0 for every bar | volume features are absent on this dataset | correct handling — absence is stored as absence; only affects datasets from this source | ACCEPTED |

