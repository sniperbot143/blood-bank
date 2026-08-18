# COST AUDIT — target: ₹0 recurring

Rule: no dependency enters this project without a line in this table. If a paid
option is added, the free fallback it replaces must still work.

## Core (required) — all ₹0

| item | cost | licence | why required | free? |
|---|---|---|---|---|
| Python 3.12+ | ₹0 | PSF | runtime | yes |
| pandas | ₹0 | BSD-3 | bar frames | yes |
| numpy | ₹0 | BSD-3 | numerics | yes |
| scipy | ₹0 | BSD-3 | Beta/Wilson intervals | yes |
| pyarrow | ₹0 | Apache-2.0 | parquet cache | yes |
| pydantic | ₹0 | MIT | config validation | yes |
| python-dotenv | ₹0 | BSD-3 | secrets from `.env` | yes |
| SQLite (stdlib) | ₹0 | public domain | setup/outcome database | yes |
| SQLAlchemy Core | ₹0 | MIT | typed DB access | yes |
| MetaTrader5 (python pkg) | ₹0 | MIT | market data (Windows only) | yes |
| MT5 terminal + demo/live acct | ₹0 | broker | data source; demo needs no deposit | yes |
| plotly | ₹0 | MIT | local HTML charts | yes |
| pytest | ₹0 | MIT | tests | yes |
| scikit-learn | ₹0 | BSD-3 | calibration (isotonic/Platt); ML only if it earns it | yes |

**Total recurring core cost: ₹0.**

## Optional (default OFF, system runs fully without them)

| item | cost | replaces / adds | fallback if absent |
|---|---|---|---|
| Claude API | pay-per-token | narrative explanation of a signal | engine prints its own reason codes |
| FastAPI + uvicorn | ₹0 | local web dashboard | Plotly HTML file opened in a browser |
| TradingView (free tier) | ₹0 | Pine v6 visual cross-check | local chart |
| Telegram bot | ₹0 | signal alerts | console + JSON file |
| VPS | ~₹400+/mo | 24/5 uptime | run on own PC |
| Paid tick data | varies | true intrabar sequencing | M1 data from MT5 (free) + pessimistic labeling |

## Deliberately rejected

Bloomberg, Refinitiv, Polygon paid tiers, AWS/Azure/GCP, hosted Postgres, paid
backtesting platforms, paid TradingView, paid signal feeds. Each is replaced by a
local free component above.

## Hidden costs to stay honest about

- **Electricity / a PC that stays on** — the only real recurring cost of running locally.
- **Windows** for the MT5 Python package (a Windows machine you already own, or Wine).
- **Time.** Building and validating this properly is the expensive input, not software.
- **Trading costs** — spread, swap, commission and slippage are modelled in every
  backtest; they are a cost of the strategy, not of the software.
