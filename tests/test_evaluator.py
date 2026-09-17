"""
Tests for evaluator/analysis.py.
All tests use in-memory SQLite + real schema. Network-requiring tests are marked 'network'.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from db.database import get_connection, init_db
from simulator.ledger import Ledger
from evaluator.analysis import (
    PATTERN_MACD_FALSE_POSITIVE,
    PATTERN_RSI_FAKE_REVERSAL,
    PATTERN_INSUFFICIENT_AGREEMENT,
    PATTERN_OVERSIZED_ENTRY_HIGH_VOL,
    PATTERN_WHIPSAW_EMA,
    PATTERN_MISSED_OPPORTUNITY,
    _classify_pattern,
    _classify_high_vol_pattern,
    _check_missed_opportunity,
    _compute_max_drawdown,
    _compute_sharpe,
    evaluate_decisions,
)

SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"

MOCK_SETTINGS = {
    "risk_limits": {
        "max_position_size_usd": 1000.0,
        "max_trades_per_day": 10,
    },
    "simulation": {
        "fee_pct": 0.1,
        "slippage_pct": 0.05,
    },
    "evaluator": {
        "missed_opportunity_lookback_days": 3,
    },
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    """In-memory SQLite seeded with real schema + starting balance."""
    db_file = tmp_path / "eval_test.db"
    init_db(db_path=db_file, schema_path=SCHEMA_PATH)
    c = get_connection(db_file)
    Ledger.initialize_ledger(c, starting_balance_usd=10_000.0)
    yield c
    c.close()


def _seed_closed_trade(
    conn: sqlite3.Connection,
    signal: str = "BUY",
    entry_price: float = 50_000.0,
    exit_price: float = 55_000.0,
    size: float = 0.002,
    cost_basis: float = 100.0,
    realized_pnl: float = 10.0,
    indicators: dict = None,
    acted_on: int = 1,
    ts_offset_minutes: int = 0,
):
    """
    Inserts a minimal decision + open position + BUY trade + SELL trade into the DB.
    Returns (decision_id, buy_trade_id, sell_trade_id).
    """
    if indicators is None:
        indicators = {
            "close": entry_price,
            "ema_20": 50100.0,
            "ema_50": 49900.0,
            "rsi_14": 28.0,
            "macd": 50.0,
            "macd_signal": 45.0,
            "macd_hist": 5.0,
        }

    base_ts = datetime(2026, 1, 1, 12, 0, 0) + timedelta(minutes=ts_offset_minutes)
    ts_str = base_ts.isoformat()

    cur = conn.cursor()

    # Decision
    cur.execute(
        """
        INSERT INTO decisions (timestamp, pair, signal, confidence, indicators_json, reasoning, acted_on)
        VALUES (?, 'BTC/USDT', ?, 0.67, ?, 'test reasoning', ?)
        """,
        (ts_str, signal, json.dumps(indicators), acted_on),
    )
    decision_id = cur.lastrowid

    # Open position
    cur.execute(
        """
        INSERT INTO positions (pair, side, status, entry_price, size, cost_basis_usd,
                               current_price, realized_pnl, created_at)
        VALUES ('BTC/USDT', 'LONG', 'CLOSED', ?, ?, ?, ?, ?, ?)
        """,
        (entry_price, size, cost_basis, exit_price, realized_pnl, ts_str),
    )
    position_id = cur.lastrowid

    # Update closed_at
    sell_ts = (base_ts + timedelta(hours=1)).isoformat()
    cur.execute(
        "UPDATE positions SET closed_at=? WHERE id=?",
        (sell_ts, position_id),
    )

    # BUY trade (uses same decision for simplicity — FK is satisfied)
    cur.execute(
        """
        INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee, slippage, executed_at)
        VALUES (?, ?, 'BTC/USDT', 'BUY', ?, ?, 0.1, 0.05, ?)
        """,
        (position_id, decision_id, entry_price, size, ts_str),
    )
    buy_trade_id = cur.lastrowid

    # SELL trade
    cur.execute(
        """
        INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee, slippage, executed_at)
        VALUES (?, ?, 'BTC/USDT', 'SELL', ?, ?, 0.1, 0.05, ?)
        """,
        (position_id, decision_id, exit_price, size, sell_ts),
    )
    sell_trade_id = cur.lastrowid

    conn.commit()
    return decision_id, buy_trade_id, sell_trade_id


def _seed_rejected_decision(
    conn: sqlite3.Connection,
    signal: str = "BUY",
    indicators: dict = None,
    ts: str = None,
):
    """Inserts a not-acted-on decision. Returns decision_id."""
    if indicators is None:
        indicators = {"close": 50_000.0, "macd_hist": 5.0, "rsi_14": 28.0}
    if ts is None:
        ts = datetime(2026, 1, 1, 10, 0, 0).isoformat()

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO decisions (timestamp, pair, signal, confidence, indicators_json, reasoning, acted_on, rejection_reason)
        VALUES (?, 'BTC/USDT', ?, 0.67, ?, 'test', 0, 'risk cap')
        """,
        (ts, signal, json.dumps(indicators)),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Unit tests: pattern classification
# ---------------------------------------------------------------------------


def test_classify_macd_false_positive_buy():
    indicators = {"macd_hist": 5.0, "rsi_14": 50.0}
    result = _classify_pattern("BUY", indicators, realized_pnl=-20.0, is_acted_on=True)
    assert result == PATTERN_MACD_FALSE_POSITIVE


def test_classify_macd_false_positive_sell():
    indicators = {"macd_hist": -3.0, "rsi_14": 55.0}
    result = _classify_pattern("SELL", indicators, realized_pnl=-15.0, is_acted_on=True)
    assert result == PATTERN_MACD_FALSE_POSITIVE


def test_classify_rsi_fake_reversal_buy():
    indicators = {"macd_hist": 5.0, "rsi_14": 25.0}  # RSI oversold
    result = _classify_pattern("BUY", indicators, realized_pnl=-10.0, is_acted_on=True)
    # RSI < 30 on a BUY loss → RSI_FAKE_REVERSAL
    assert result == PATTERN_RSI_FAKE_REVERSAL


def test_classify_rsi_fake_reversal_sell():
    indicators = {"macd_hist": -3.0, "rsi_14": 78.0}  # RSI overbought
    result = _classify_pattern("SELL", indicators, realized_pnl=-8.0, is_acted_on=True)
    assert result == PATTERN_RSI_FAKE_REVERSAL


def test_classify_whipsaw_ema_buy():
    indicators = {"ema_20": 50200.0, "ema_50": 49800.0, "macd_hist": None, "rsi_14": None}
    result = _classify_pattern("BUY", indicators, realized_pnl=-5.0, is_acted_on=True)
    assert result == PATTERN_WHIPSAW_EMA


def test_classify_whipsaw_ema_sell():
    indicators = {"ema_20": 49500.0, "ema_50": 50500.0, "macd_hist": None, "rsi_14": None}
    result = _classify_pattern("SELL", indicators, realized_pnl=-5.0, is_acted_on=True)
    assert result == PATTERN_WHIPSAW_EMA


def test_classify_insufficient_agreement_fallback():
    # No usable indicator data → falls back to INSUFFICIENT_AGREEMENT
    indicators = {}
    result = _classify_pattern("BUY", indicators, realized_pnl=-1.0, is_acted_on=True)
    assert result == PATTERN_INSUFFICIENT_AGREEMENT


def test_classify_no_pattern_on_profit():
    indicators = {"macd_hist": 5.0, "rsi_14": 25.0}
    result = _classify_pattern("BUY", indicators, realized_pnl=50.0, is_acted_on=True)
    assert result is None


def test_classify_no_pattern_when_not_acted_on():
    indicators = {"macd_hist": 5.0}
    result = _classify_pattern("BUY", indicators, realized_pnl=-10.0, is_acted_on=False)
    assert result is None


def test_classify_oversized_entry_high_vol():
    indicators = {"close": 50_000.0, "macd_hist": 500.0}  # 500/50000 = 1% >> 0.5%
    result = _classify_high_vol_pattern(indicators, size_usd=850.0, settings=MOCK_SETTINGS)
    assert result == PATTERN_OVERSIZED_ENTRY_HIGH_VOL


def test_classify_no_oversized_when_low_vol():
    indicators = {"close": 50_000.0, "macd_hist": 50.0}  # 50/50000 = 0.1% << 0.5%
    result = _classify_high_vol_pattern(indicators, size_usd=850.0, settings=MOCK_SETTINGS)
    assert result is None


def test_classify_no_oversized_when_small_position():
    indicators = {"close": 50_000.0, "macd_hist": 500.0}
    result = _classify_high_vol_pattern(indicators, size_usd=200.0, settings=MOCK_SETTINGS)
    assert result is None


# ---------------------------------------------------------------------------
# Unit tests: metrics helpers
# ---------------------------------------------------------------------------


def test_max_drawdown_basic():
    pnls = [10.0, -30.0, 20.0, -5.0]
    dd = _compute_max_drawdown(pnls)
    # cumulative: 10, -20, 0, -5 → peak=10, trough=-20 → dd=30
    assert dd == pytest.approx(30.0)


def test_max_drawdown_empty():
    assert _compute_max_drawdown([]) == 0.0


def test_max_drawdown_all_positive():
    assert _compute_max_drawdown([5.0, 10.0, 15.0]) == 0.0


def test_sharpe_basic():
    pnls = [1.0, 2.0, 1.5, 2.5, 1.0]
    sharpe = _compute_sharpe(pnls)
    assert sharpe is not None
    assert sharpe > 0  # positive mean, positive SR


def test_sharpe_returns_none_on_zero_variance():
    pnls = [5.0, 5.0, 5.0]
    assert _compute_sharpe(pnls) is None


def test_sharpe_single_value():
    assert _compute_sharpe([10.0]) is None


# ---------------------------------------------------------------------------
# Tests: missed-opportunity logic (fixture prices, no network)
# ---------------------------------------------------------------------------


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_missed_opportunity_detected_when_price_moves_favorably(mock_cfg, conn):
    """
    Rejected BUY at T=10:00, SELL trade exists at T+6h at higher price (within 3-day window).
    Should be flagged as missed opportunity.
    """
    base_ts = datetime(2026, 1, 1, 10, 0, 0)
    rejected_ts = base_ts.isoformat()

    # Seed rejected decision at 10:00
    indicators = {"close": 50_000.0}
    _seed_rejected_decision(conn, signal="BUY", indicators=indicators, ts=rejected_ts)

    # _seed_closed_trade offsets from datetime(2026,1,1,12,0,0) + ts_offset_minutes.
    # offset=360 → BUY trade at 18:00, SELL trade at 19:00 — both after rejected ts 10:00.
    # The SELL trade price (52_000) > entry_price (50_000) → favorable for BUY.
    _seed_closed_trade(
        conn,
        signal="BUY",
        entry_price=50_000.0,
        exit_price=52_000.0,
        realized_pnl=100.0,
        ts_offset_minutes=360,  # base 12:00 + 6h → BUY@18:00, SELL@19:00
    )

    cur = conn.cursor()
    cur.execute("SELECT * FROM decisions WHERE acted_on=0 LIMIT 1")
    row = cur.fetchone()

    result = _check_missed_opportunity(row, conn, lookback_days=3, fee_pct=0.1)
    assert result is True


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_missed_opportunity_not_flagged_when_price_drops(mock_cfg, conn):
    """
    Rejected BUY but later price is lower → not a missed opportunity.
    """
    base_ts = datetime(2026, 1, 2, 10, 0, 0)
    rejected_ts = base_ts.isoformat()

    indicators = {"close": 50_000.0}
    _seed_rejected_decision(conn, signal="BUY", indicators=indicators, ts=rejected_ts)

    # Later trade at LOWER price
    _seed_closed_trade(
        conn,
        signal="BUY",
        entry_price=50_000.0,
        exit_price=47_000.0,  # lower
        realized_pnl=-60.0,
        ts_offset_minutes=12 * 60,  # 12h later, same day
    )

    cur = conn.cursor()
    cur.execute("SELECT * FROM decisions WHERE acted_on=0 LIMIT 1")
    row = cur.fetchone()

    result = _check_missed_opportunity(row, conn, lookback_days=3, fee_pct=0.1)
    assert result is False


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_hold_decision_never_flagged_as_missed(mock_cfg, conn):
    """HOLD decisions have no directional intent — must never be missed opportunity."""
    ts = datetime(2026, 1, 3, 10, 0, 0).isoformat()
    _seed_rejected_decision(conn, signal="HOLD", indicators={"close": 50_000.0}, ts=ts)

    # Plant a favorable price
    _seed_closed_trade(
        conn,
        entry_price=50_000.0,
        exit_price=55_000.0,
        realized_pnl=100.0,
        ts_offset_minutes=6 * 60,
    )

    cur = conn.cursor()
    cur.execute("SELECT * FROM decisions WHERE acted_on=0 LIMIT 1")
    row = cur.fetchone()

    result = _check_missed_opportunity(row, conn, lookback_days=3, fee_pct=0.1)
    assert result is False


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_missed_opportunity_outside_lookback_window_ignored(mock_cfg, conn):
    """Price improvement beyond the lookback window must not count."""
    base_ts = datetime(2026, 1, 4, 10, 0, 0)
    rejected_ts = base_ts.isoformat()

    indicators = {"close": 50_000.0}
    _seed_rejected_decision(conn, signal="BUY", indicators=indicators, ts=rejected_ts)

    # Price moves favorably but AFTER the 3-day window
    far_future = base_ts + timedelta(days=5)
    cur = conn.cursor()

    # Insert a decision + position + trades at far future (no helper needed for exact timing)
    cur.execute(
        """
        INSERT INTO decisions (timestamp, pair, signal, confidence, indicators_json, reasoning, acted_on)
        VALUES (?, 'BTC/USDT', 'BUY', 0.67, ?, 'future', 1)
        """,
        (far_future.isoformat(), json.dumps({"close": 52_000.0})),
    )
    future_dec_id = cur.lastrowid

    cur.execute(
        """
        INSERT INTO positions (pair, side, status, entry_price, size, cost_basis_usd,
                               current_price, realized_pnl, created_at, closed_at)
        VALUES ('BTC/USDT','LONG','CLOSED',50000,0.002,100,52000,40, ?,?)
        """,
        (far_future.isoformat(), (far_future + timedelta(hours=1)).isoformat()),
    )
    future_pos_id = cur.lastrowid

    sell_ts = (far_future + timedelta(hours=1)).isoformat()
    cur.execute(
        """
        INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee, slippage, executed_at)
        VALUES (?, ?, 'BTC/USDT','BUY',50000,0.002,0.1,0.05,?)
        """,
        (future_pos_id, future_dec_id, far_future.isoformat()),
    )
    cur.execute(
        """
        INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee, slippage, executed_at)
        VALUES (?, ?, 'BTC/USDT','SELL',52000,0.002,0.1,0.05,?)
        """,
        (future_pos_id, future_dec_id, sell_ts),
    )
    conn.commit()

    cur.execute("SELECT * FROM decisions WHERE acted_on=0 LIMIT 1")
    row = cur.fetchone()

    # lookback=3 days, future price is 5 days out — should NOT be flagged
    result = _check_missed_opportunity(row, conn, lookback_days=3, fee_pct=0.1)
    assert result is False


# ---------------------------------------------------------------------------
# Integration: evaluations table writes
# ---------------------------------------------------------------------------


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_writes_evaluation_row(mock_cfg, conn):
    """evaluate_decisions() must insert exactly one row into evaluations."""
    _seed_closed_trade(conn, realized_pnl=50.0)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM evaluations")
    before = cur.fetchone()["cnt"]

    evaluate_decisions(conn)

    cur.execute("SELECT COUNT(*) AS cnt FROM evaluations")
    after = cur.fetchone()["cnt"]
    assert after == before + 1


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_row_fields_are_correct_types(mock_cfg, conn):
    """Evaluation row must have correct field types and non-negative counts."""
    _seed_closed_trade(conn, realized_pnl=-30.0)
    evaluate_decisions(conn)

    cur = conn.cursor()
    cur.execute("SELECT * FROM evaluations ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()

    assert isinstance(row["total_trades"], int)
    assert isinstance(row["win_rate"], float)
    assert 0.0 <= row["win_rate"] <= 1.0
    assert isinstance(row["total_pnl"], float)
    assert isinstance(row["max_drawdown"], float)
    assert row["max_drawdown"] >= 0.0
    # mistakes_json must be valid JSON list
    mistakes = json.loads(row["mistakes_json"])
    assert isinstance(mistakes, list)


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_empty_db_writes_row(mock_cfg, conn):
    """Even with no trades/decisions, one evaluation row must be written."""
    evaluate_decisions(conn)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM evaluations")
    assert cur.fetchone()["cnt"] == 1


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_respects_fk_constraints(mock_cfg, conn):
    """Evaluation row must satisfy FK constraints (none on evaluations — check schema integrity)."""
    _seed_closed_trade(conn, realized_pnl=25.0)
    summary = evaluate_decisions(conn)

    # FK pragma is on — if evaluations had FK issues, commit would raise
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    result = cur.fetchone()[0]
    assert result == "ok"

    assert "Evaluator Report" in summary


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_classifies_macd_false_positive(mock_cfg, conn):
    """A loss trade with neutral RSI + positive MACD on BUY gets MACD_FALSE_POSITIVE."""
    indicators = {
        "close": 50_000.0,
        "ema_20": 50100.0, "ema_50": 49900.0,
        "rsi_14": 50.0,   # neutral
        "macd_hist": 5.0, # positive → MACD said BUY
    }
    _seed_closed_trade(conn, signal="BUY", realized_pnl=-20.0, indicators=indicators)
    evaluate_decisions(conn)

    cur = conn.cursor()
    cur.execute("SELECT mistakes_json FROM evaluations ORDER BY id DESC LIMIT 1")
    mistakes = json.loads(cur.fetchone()["mistakes_json"])
    assert PATTERN_MACD_FALSE_POSITIVE in mistakes


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_flags_missed_opportunity(mock_cfg, conn):
    """A rejected BUY where price later rises must appear as MISSED_OPPORTUNITY."""
    # Rejected at 08:00; SELL trade at 15:00 (same day) at higher price — within 3-day window
    rejected_ts = datetime(2026, 1, 1, 8, 0, 0).isoformat()
    _seed_rejected_decision(
        conn, signal="BUY",
        indicators={"close": 50_000.0},
        ts=rejected_ts,
    )
    # ts_offset_minutes=120 → base(12:00)+2h=14:00 BUY, SELL at 15:00; both after 08:00
    _seed_closed_trade(
        conn,
        entry_price=50_000.0,
        exit_price=52_000.0,
        realized_pnl=100.0,
        ts_offset_minutes=120,
    )

    evaluate_decisions(conn)

    cur = conn.cursor()
    cur.execute("SELECT mistakes_json FROM evaluations ORDER BY id DESC LIMIT 1")
    mistakes = json.loads(cur.fetchone()["mistakes_json"])
    assert PATTERN_MISSED_OPPORTUNITY in mistakes


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_evaluate_decisions_multiple_trades_aggregated(mock_cfg, conn):
    """Multiple trades should aggregate into one evaluation row with correct total_pnl."""
    _seed_closed_trade(conn, realized_pnl=30.0, ts_offset_minutes=0)
    _seed_closed_trade(conn, realized_pnl=-10.0, ts_offset_minutes=120)
    _seed_closed_trade(conn, realized_pnl=20.0, ts_offset_minutes=240)

    evaluate_decisions(conn)

    cur = conn.cursor()
    cur.execute("SELECT total_trades, total_pnl, win_rate FROM evaluations ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    assert row["total_trades"] == 3
    assert row["total_pnl"] == pytest.approx(40.0, rel=1e-6)
    assert row["win_rate"] == pytest.approx(2 / 3, abs=1e-4)


@patch("evaluator.analysis.load_settings", return_value=MOCK_SETTINGS)
def test_summary_string_returned_and_printed(mock_cfg, conn, capsys):
    """evaluate_decisions must return and print a plain-English summary."""
    _seed_closed_trade(conn, realized_pnl=15.0)
    summary = evaluate_decisions(conn)

    captured = capsys.readouterr()
    assert "Evaluator Report" in captured.out
    assert "Evaluator Report" in summary
    assert "Win rate" in summary
