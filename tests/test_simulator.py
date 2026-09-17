"""
Tests for simulator/execution.py — one test per scenario.
Uses an in-memory/temp SQLite DB seeded with the real schema.
"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from db.database import get_connection, init_db
from simulator.ledger import Ledger
from simulator.execution import execute_decision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"

# A realistic settings dict used by all tests (patched in)
MOCK_SETTINGS = {
    "risk_limits": {
        "max_position_size_usd": 1000.0,
        "max_trades_per_day": 10,
    },
    "simulation": {
        "fee_pct": 0.1,
        "slippage_pct": 0.05,
    },
}


def make_decision(signal: str, confidence: float = 0.67) -> dict:
    """Construct a minimal decision dict matching generate_signal() output."""
    return {
        "signal": signal,
        "confidence": confidence,
        "reasoning": f"Test {signal} signal",
        "indicators_snapshot": {"close": 50000.0},
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    """Provides a temp-file SQLite connection seeded with schema + starting balance."""
    db_file = tmp_path / "test_paper_trader.db"
    init_db(db_path=db_file, schema_path=SCHEMA_PATH)
    c = get_connection(db_file)
    Ledger.initialize_ledger(c, starting_balance_usd=10_000.0)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_hold_signal_does_not_execute(mock_cfg, conn):
    """HOLD signal must return executed=False immediately without writing anything."""
    result = execute_decision(conn, make_decision("HOLD"), current_price=50_000.0, pair="BTC/USDT")

    assert result["executed"] is False
    assert result["reason"] == "signal is HOLD"
    assert result["trade_id"] is None

    # Nothing written to decisions or trades
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS cnt FROM decisions")
    assert cursor.fetchone()["cnt"] == 0
    cursor.execute("SELECT COUNT(*) AS cnt FROM trades")
    assert cursor.fetchone()["cnt"] == 0


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_buy_within_limits_executes_and_updates_balance(mock_cfg, conn):
    """A BUY within risk limits must execute, write rows, and reduce available balance."""
    result = execute_decision(conn, make_decision("BUY"), current_price=50_000.0, pair="BTC/USDT")

    assert result["executed"] is True
    assert result["trade_id"] is not None

    # decisions row written and acted_on=1
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM decisions WHERE id = 1")
    dec = cursor.fetchone()
    assert dec["signal"] == "BUY"
    assert dec["acted_on"] == 1

    # positions row opened
    cursor.execute("SELECT * FROM positions WHERE id = 1")
    pos = cursor.fetchone()
    assert pos["status"] == "OPEN"
    assert pos["side"] == "LONG"

    # trades row written
    cursor.execute("SELECT * FROM trades WHERE id = ?", (result["trade_id"],))
    trade = cursor.fetchone()
    assert trade["side"] == "BUY"

    # Balance available decreased
    bal = Ledger.get_current_balance(conn)
    assert bal["available_usd"] < 10_000.0


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_buy_exceeding_max_position_size_is_rejected(mock_cfg, conn):
    """10% of available ($1000) exceeds a $500 cap — must be rejected with correct reason."""
    # Balance = $10,000, so 10% = $1,000 which exceeds cap of $500
    tight_settings = {
        "risk_limits": {"max_position_size_usd": 500.0, "max_trades_per_day": 10},
        "simulation": {"fee_pct": 0.1, "slippage_pct": 0.05},
    }
    with patch("simulator.execution.load_settings", return_value=tight_settings):
        result = execute_decision(conn, make_decision("BUY"), current_price=50_000.0, pair="BTC/USDT")

    assert result["executed"] is False
    assert result["reason"] == "exceeds max_position_size_usd"
    assert result["trade_id"] is None

    # Confirm nothing was written to the DB
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS cnt FROM decisions")
    assert cursor.fetchone()["cnt"] == 0


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_max_trades_per_day_limit_is_enforced(mock_cfg, conn):
    """When today's trade count >= max_trades_per_day, BUY must be rejected."""
    tight_settings = {
        "risk_limits": {"max_position_size_usd": 1000.0, "max_trades_per_day": 1},
        "simulation": {"fee_pct": 0.1, "slippage_pct": 0.05},
    }
    with patch("simulator.execution.load_settings", return_value=tight_settings):
        # First trade succeeds
        r1 = execute_decision(conn, make_decision("BUY"), current_price=50_000.0, pair="BTC/USDT")
        assert r1["executed"] is True

        # Second trade must be rejected due to daily cap
        r2 = execute_decision(conn, make_decision("BUY"), current_price=50_000.0, pair="BTC/USDT")
        assert r2["executed"] is False
        assert r2["reason"] == "max_trades_per_day reached"
        assert r2["trade_id"] is None


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_sell_closes_open_position_and_records_pnl(mock_cfg, conn):
    """SELL must close the existing open position and record realized PnL."""
    # First open a position via BUY
    r_buy = execute_decision(conn, make_decision("BUY"), current_price=50_000.0, pair="BTC/USDT")
    assert r_buy["executed"] is True

    # Now SELL at a higher price (profit scenario)
    r_sell = execute_decision(conn, make_decision("SELL"), current_price=55_000.0, pair="BTC/USDT")
    assert r_sell["executed"] is True
    assert r_sell["trade_id"] is not None

    # Position must be CLOSED
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM positions ORDER BY id")
    pos = cursor.fetchone()
    assert pos["status"] == "CLOSED"
    assert pos["realized_pnl"] is not None

    # trades table must have both BUY and SELL rows
    cursor.execute("SELECT COUNT(*) AS cnt FROM trades")
    assert cursor.fetchone()["cnt"] == 2

    # SELL trade side check
    cursor.execute("SELECT side FROM trades ORDER BY id DESC LIMIT 1")
    assert cursor.fetchone()["side"] == "SELL"


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_fee_and_slippage_are_applied_to_execution_price(mock_cfg, conn):
    """Exact numeric execution_price must match current_price * (1 + slippage_pct/100) for BUY."""
    current_price = 50_000.0
    slippage_pct = MOCK_SETTINGS["simulation"]["slippage_pct"]  # 0.05
    expected_buy_price = current_price * (1 + slippage_pct / 100.0)  # 50_025.0

    execute_decision(conn, make_decision("BUY"), current_price=current_price, pair="BTC/USDT")

    cursor = conn.cursor()
    cursor.execute("SELECT price FROM trades ORDER BY id DESC LIMIT 1")
    actual_price = cursor.fetchone()["price"]
    assert actual_price == pytest.approx(expected_buy_price, rel=1e-9)

    # Also verify fee was applied: fee = size_usd * fee_pct/100
    fee_pct = MOCK_SETTINGS["simulation"]["fee_pct"]  # 0.1
    # size_usd = min(10000 * 0.10, 1000) = 1000
    size_usd = min(10_000.0 * 0.10, 1000.0)
    expected_fee = size_usd * (fee_pct / 100.0)  # 1.0

    cursor.execute("SELECT fee FROM trades ORDER BY id DESC LIMIT 1")
    actual_fee = cursor.fetchone()["fee"]
    assert actual_fee == pytest.approx(expected_fee, rel=1e-9)

    # Verify SELL slippage direction
    # Open balance after buy
    bal_after_buy = Ledger.get_current_balance(conn)
    r_sell = execute_decision(conn, make_decision("SELL"), current_price=current_price, pair="BTC/USDT")
    assert r_sell["executed"] is True

    expected_sell_price = current_price * (1 - slippage_pct / 100.0)  # 49_975.0
    cursor.execute("SELECT price FROM trades ORDER BY id DESC LIMIT 1")
    actual_sell_price = cursor.fetchone()["price"]
    assert actual_sell_price == pytest.approx(expected_sell_price, rel=1e-9)


@patch("simulator.execution.load_settings", return_value=MOCK_SETTINGS)
def test_failed_transaction_rolls_back_completely(mock_cfg, conn):
    """A mid-transaction error must roll back all partial writes (no orphan rows)."""
    # Patch Ledger.record_balance_snapshot to blow up mid-transaction
    original_snap = Ledger.record_balance_snapshot

    call_count = {"n": 0}

    def exploding_snapshot(c, total, avail, locked):
        call_count["n"] += 1
        raise RuntimeError("Simulated mid-transaction failure")

    with patch.object(Ledger, "record_balance_snapshot", side_effect=exploding_snapshot):
        result = execute_decision(conn, make_decision("BUY"), current_price=50_000.0, pair="BTC/USDT")

    assert result["executed"] is False
    assert "transaction error" in result["reason"]
    assert result["trade_id"] is None

    # No rows should exist in any table (full rollback)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS cnt FROM decisions")
    assert cursor.fetchone()["cnt"] == 0, "decisions table should have 0 rows after rollback"

    cursor.execute("SELECT COUNT(*) AS cnt FROM positions")
    assert cursor.fetchone()["cnt"] == 0, "positions table should have 0 rows after rollback"

    cursor.execute("SELECT COUNT(*) AS cnt FROM trades")
    assert cursor.fetchone()["cnt"] == 0, "trades table should have 0 rows after rollback"

    # Balance snapshot count should still be 1 (only the initial from fixture)
    cursor.execute("SELECT COUNT(*) AS cnt FROM balance")
    assert cursor.fetchone()["cnt"] == 1, "balance table should have only the initial row after rollback"
