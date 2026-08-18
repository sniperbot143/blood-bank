# Changelog

Format: newest first. One entry per phase or fix.

## [0.1.0-design] — 2026-08-18

### Added
- Phase 0 design freeze. No application code.
- `docs/ARCHITECTURE.md` — 9-layer architecture, data contract, module map,
  five look-ahead guards, storage schema, runtime modes, MT5 execution path,
  seven named weaknesses.
- `docs/SMC_DEFINITIONS.md` — deterministic definitions for swings, structure,
  BOS, CHOCH, MSS, displacement, liquidity, sweeps, FVG, IFVG, order blocks,
  breakers, premium/discount, sessions, regime, and the v0.1 setup taxonomy.
- `docs/PROBABILITY_METHODOLOGY.md` — setup database, outcome labeling, tiered
  similarity back-off, beta-binomial (Jeffreys) estimator with Wilson and
  block-bootstrap intervals, reliability tiers, calibration, no-look-ahead rules,
  decision engine thresholds and vetoes.
- `docs/PHASE_1_PLAN.md` — proposed first implementation phase.
- `COST_AUDIT.md` — ₹0 core stack; optional and rejected components.
- `README.md`, `docs/KNOWN_ISSUES.md`.

### Decisions locked
- Setup score and probability are separate numbers, never derived from each other.
- Every SMC object carries `formed_at_index` and `confirmed_at_index`; only
  confirmed objects are readable by the setup builder.
- Probability comparables are filtered by `resolved_at < signal_time`.
- Intrabar TP/SL ambiguity resolves to `SL_FIRST` unless M1 data is present.
- MSS requires displacement; CHOCH does not. They are distinct event types.

### Pending approval
- Phase 1 scope (data layer). No code is written until approved.
