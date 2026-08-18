"""Shared numeric primitives used by several detector packages (ATR, ...).

Architecture note: detectors may import `config/`, `common/` and numpy/pandas
-- nothing else. This keeps every detector unit-testable on a hand-built frame.
"""
