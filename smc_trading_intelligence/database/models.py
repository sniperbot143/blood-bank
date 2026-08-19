"""The setup census: every candidate ever generated, winners and losers.

SQLite, stdlib only, one file. Three tables:

    setups     one row per candidate, with the indexed similarity keys as
               columns and the full feature vector as JSON
    outcomes   one row per candidate, including `resolved_at` -- the field the
               probability engine filters on so it never uses a trade whose
               result was still unknown
    runs       what produced a batch: rules hash, code version, config

Losing setups are never discarded. A database of winners would make every
probability 100% and every one of them a lie.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtesting.labeling import LabelledOutcome, Outcome
from signals.setups import SetupCandidate

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    rules_hash   TEXT NOT NULL,
    bars         INTEGER NOT NULL,
    first_bar    TEXT,
    last_bar     TEXT,
    note         TEXT,
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS setups (
    setup_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(run_id),
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    signal_time  TEXT NOT NULL,
    signal_index INTEGER NOT NULL,
    family       TEXT NOT NULL,
    setup_type   TEXT NOT NULL,
    direction    TEXT NOT NULL,
    regime_key   TEXT,
    session      TEXT,
    pd_zone      TEXT,
    htf_agreement TEXT,
    poi_type     TEXT,
    entry        REAL, stop_loss REAL,
    take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
    risk_reward  REAL, risk_atr REAL, atr REAL,
    superseded   INTEGER NOT NULL DEFAULT 0,
    features     TEXT NOT NULL,
    rules_hash   TEXT NOT NULL,
    UNIQUE (symbol, timeframe, signal_time, setup_type, rules_hash)
);

CREATE TABLE IF NOT EXISTS outcomes (
    setup_id       INTEGER PRIMARY KEY REFERENCES setups(setup_id) ON DELETE CASCADE,
    outcome        TEXT NOT NULL,
    r_multiple     REAL,
    fill_index     INTEGER,
    resolved_index INTEGER,
    resolved_at    TEXT,
    bars_to_result INTEGER,
    mae_r          REAL,
    mfe_r          REAL,
    ambiguous      INTEGER NOT NULL DEFAULT 0,
    cost_r         REAL
);

CREATE INDEX IF NOT EXISTS idx_setups_lookup
    ON setups (symbol, timeframe, setup_type, direction);
CREATE INDEX IF NOT EXISTS idx_setups_family
    ON setups (symbol, timeframe, family, direction);
CREATE INDEX IF NOT EXISTS idx_setups_regime
    ON setups (symbol, timeframe, setup_type, direction, regime_key);
CREATE INDEX IF NOT EXISTS idx_outcomes_resolved
    ON outcomes (resolved_at);
"""


@dataclass
class StoreStats:
    setups: int = 0
    outcomes: int = 0
    inserted: int = 0
    skipped: int = 0


class SetupStore:
    """Read/write access to the setup census."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SetupStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def start_run(self, *, symbol: str, timeframe: str, rules_hash: str, bars: int,
                  first_bar: pd.Timestamp | None = None,
                  last_bar: pd.Timestamp | None = None, note: str = "") -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs (created_at, symbol, timeframe, rules_hash, bars, "
            "first_bar, last_bar, note, schema_version) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), symbol, timeframe, rules_hash, bars,
             first_bar.isoformat() if first_bar is not None else None,
             last_bar.isoformat() if last_bar is not None else None,
             note, SCHEMA_VERSION),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def save(self, run_id: int, candidate: SetupCandidate, outcome: LabelledOutcome,
             rules_hash: str) -> int | None:
        """Insert one candidate and its outcome. Duplicates are skipped."""
        features = {k: (None if isinstance(v, float) and v != v else v)
                    for k, v in candidate.features.values.items()}
        levels = candidate.levels
        try:
            cursor = self.connection.execute(
                "INSERT INTO setups (run_id, symbol, timeframe, signal_time, signal_index, "
                "family, setup_type, direction, regime_key, session, pd_zone, htf_agreement, "
                "poi_type, entry, stop_loss, take_profit_1, take_profit_2, take_profit_3, "
                "risk_reward, risk_atr, atr, superseded, features, rules_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, candidate.symbol, candidate.timeframe,
                 candidate.signal_time.isoformat(), candidate.signal_index,
                 candidate.family.value, candidate.setup_type, candidate.direction,
                 features.get("regime_key"), features.get("session"),
                 features.get("pd_zone"), features.get("htf_bias_agreement"),
                 features.get("poi_type"), levels.entry, levels.stop_loss,
                 levels.take_profit_1, levels.take_profit_2, levels.take_profit_3,
                 levels.rr1, levels.risk_atr, features.get("atr"),
                 int(candidate.superseded), json.dumps(features, default=str), rules_hash),
            )
        except sqlite3.IntegrityError:
            return None

        setup_id = int(cursor.lastrowid)
        data = outcome.as_dict()
        self.connection.execute(
            "INSERT OR REPLACE INTO outcomes (setup_id, outcome, r_multiple, fill_index, "
            "resolved_index, resolved_at, bars_to_result, mae_r, mfe_r, ambiguous, cost_r) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (setup_id, data["outcome"], _num(data["r_multiple"]), data["fill_index"],
             data["resolved_index"],
             data["resolved_at"].isoformat() if data["resolved_at"] is not None else None,
             data["bars_to_result"], _num(data["mae_r"]), _num(data["mfe_r"]),
             data["ambiguous"], _num(data["cost_r"])),
        )
        return setup_id

    def save_many(self, run_id: int, candidates: list[SetupCandidate],
                  outcomes: list[LabelledOutcome], rules_hash: str) -> StoreStats:
        stats = StoreStats()
        for candidate, outcome in zip(candidates, outcomes):
            if self.save(run_id, candidate, outcome, rules_hash) is None:
                stats.skipped += 1
            else:
                stats.inserted += 1
        self.connection.commit()
        stats.setups = self.count()
        return stats

    # -- reading -----------------------------------------------------------

    def count(self, *, resolved_only: bool = False) -> int:
        query = "SELECT COUNT(*) FROM setups s JOIN outcomes o USING (setup_id)"
        if resolved_only:
            query += " WHERE o.resolved_at IS NOT NULL"
        return int(self.connection.execute(query).fetchone()[0])

    def query(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        setup_type: str | None = None,
        family: str | None = None,
        direction: str | None = None,
        regime_key: str | None = None,
        session: str | None = None,
        pd_zone: str | None = None,
        resolved_before: pd.Timestamp | str | None = None,
        include_superseded: bool = False,
        resolved_only: bool = True,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Fetch comparable setups.

        `resolved_before` is the as-of guard: it filters on `resolved_at`, not
        on signal time, so a trade that was still open when the new signal
        fired contributes nothing (docs/PROBABILITY_METHODOLOGY.md §7).
        """
        clauses: list[str] = []
        params: list = []

        for column, value in (
            ("s.symbol", symbol), ("s.timeframe", timeframe), ("s.setup_type", setup_type),
            ("s.family", family), ("s.direction", direction), ("s.regime_key", regime_key),
            ("s.session", session), ("s.pd_zone", pd_zone),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)

        if not include_superseded:
            clauses.append("s.superseded = 0")
        if resolved_only:
            clauses.append("o.resolved_at IS NOT NULL")
        if resolved_before is not None:
            stamp = (resolved_before.isoformat() if isinstance(resolved_before, pd.Timestamp)
                     else str(resolved_before))
            clauses.append("o.resolved_at < ?")
            params.append(stamp)

        query = (
            "SELECT s.*, o.outcome, o.r_multiple, o.resolved_at, o.bars_to_result, "
            "o.mae_r, o.mfe_r, o.ambiguous FROM setups s JOIN outcomes o USING (setup_id)"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY o.resolved_at"
        if limit:
            query += f" LIMIT {int(limit)}"

        rows = self.connection.execute(query, params).fetchall()
        if not rows:
            return pd.DataFrame(columns=["setup_id", "setup_type", "outcome", "r_multiple"])
        frame = pd.DataFrame([dict(row) for row in rows])
        frame["resolved_at"] = pd.to_datetime(frame["resolved_at"], utc=True, errors="coerce")
        frame["signal_time"] = pd.to_datetime(frame["signal_time"], utc=True, errors="coerce")
        return frame

    def summary(self) -> pd.DataFrame:
        rows = self.connection.execute(
            "SELECT s.symbol, s.timeframe, s.family, s.direction, COUNT(*) AS n, "
            "SUM(CASE WHEN o.outcome LIKE 'TP%' THEN 1 ELSE 0 END) AS wins, "
            "AVG(o.r_multiple) AS avg_r "
            "FROM setups s JOIN outcomes o USING (setup_id) "
            "WHERE s.superseded = 0 AND o.resolved_at IS NOT NULL "
            "GROUP BY s.symbol, s.timeframe, s.family, s.direction ORDER BY n DESC"
        ).fetchall()
        return pd.DataFrame([dict(row) for row in rows]) if rows else pd.DataFrame()


def _num(value) -> float | None:
    """SQLite has no NaN; store it as NULL so it reads back as missing."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number
