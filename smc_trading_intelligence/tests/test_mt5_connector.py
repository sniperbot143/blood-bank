"""MT5 connector: the parts that are testable without a Windows terminal.

The connector must (a) import cleanly on any OS, (b) fail with an actionable
message rather than a stack trace, and (c) generate the right broker-suffix
candidates.
"""

from __future__ import annotations

import pytest

from data import mt5_connector as mc


def test_module_imports_without_metatrader5():
    # On Linux/macOS MT5_AVAILABLE is False and that must not be an error.
    assert isinstance(mc.MT5_AVAILABLE, bool)


def test_suffix_candidates_cover_common_brokers():
    candidates = mc.suffix_candidates("XAUUSD")
    for expected in ["XAUUSD", "XAUUSDM", "XAUUSD.M", "XAUUSDMICRO", "XAUUSD#"]:
        assert expected in candidates
    assert candidates[0] == "XAUUSD"          # exact name tried first
    assert len(candidates) == len(set(candidates))  # no duplicates


def test_suffix_candidates_normalise_input():
    assert "EURUSDM" in mc.suffix_candidates(" eurusd ")


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, 0.0), (7200, 2.0), (10800, 3.0), (-3600, -1.0), (1800, 0.5), (100, 0.0)],
)
def test_server_offset_rounds_to_half_hours(seconds, expected):
    assert mc.round_offset_hours(seconds) == expected


@pytest.mark.skipif(mc.MT5_AVAILABLE, reason="MT5 present; the failure path can't be exercised")
def test_connect_without_the_package_explains_the_csv_alternative():
    with pytest.raises(mc.MT5Unavailable, match="Windows"):
        mc.MT5Connector().connect()


@pytest.mark.skipif(mc.MT5_AVAILABLE, reason="MT5 present")
def test_calls_before_connect_are_refused():
    with pytest.raises(mc.MT5Unavailable):
        mc.MT5Connector().symbols()


def test_no_order_functions_are_exposed():
    """READ ONLY by construction -- Phase 1 must not be able to trade."""
    forbidden = {"order_send", "order_check", "place_order", "buy", "sell", "close_position"}
    assert forbidden.isdisjoint(dir(mc.MT5Connector))
    assert forbidden.isdisjoint(dir(mc))
