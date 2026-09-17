"""
Simulator execution engine for paper trader.
Applies risk caps, executes simulated trades, and maintains the balance ledger.
"""
import json
import logging
import sqlite3
from datetime import date
from typing import Optional

from config.config import load_settings
from simulator.ledger import Ledger

logger = logging.getLogger(__name__)


def execute_decision(conn: sqlite3.Connection, decision: dict, current_price: float, pair: str) -> dict:
    """
    Takes a decision dict from generate_signal(), applies risk caps, and
    either executes a simulated trade or rejects it.
    Returns: {"executed": bool, "reason": str, "trade_id": int|None}
    """
    settings = load_settings()
    max_position_size_usd: float = settings["risk_limits"]["max_position_size_usd"]
    max_trades_per_day: int = settings["risk_limits"]["max_trades_per_day"]
    fee_pct: float = settings["simulation"]["fee_pct"]
    slippage_pct: float = settings["simulation"]["slippage_pct"]

    # -------------------------------------------------------------------------
    # STEP 2 — Risk cap checks (no DB writes, plain if/return, no exceptions)
    # -------------------------------------------------------------------------

    # 2a. HOLD signals are never executed
    if decision["signal"] == "HOLD":
        return {"executed": False, "reason": "signal is HOLD", "trade_id": None}

    # 2b. Daily trade count cap
    cursor = conn.cursor()
    today_str = date.today().isoformat()  # 'YYYY-MM-DD'
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM trades WHERE DATE(executed_at) = ?",
        (today_str,),
    )
    trades_today: int = cursor.fetchone()["cnt"]
    if trades_today >= max_trades_per_day:
        return {"executed": False, "reason": "max_trades_per_day reached", "trade_id": None}

    # 2c. Sizing rule: use 10% of available balance as the intended position size.
    #     If that raw size exceeds max_position_size_usd, reject; otherwise trade the raw size.
    balance = Ledger.get_current_balance(conn)
    available_usd: float = balance["available_usd"]
    size_usd: float = available_usd * 0.10

    # 2d. Hard cap check
    if size_usd > max_position_size_usd:
        return {"executed": False, "reason": "exceeds max_position_size_usd", "trade_id": None}

    # Guard: not enough available balance to trade
    if size_usd <= 0:
        return {"executed": False, "reason": "insufficient available balance", "trade_id": None}

    # -------------------------------------------------------------------------
    # STEP 3 — Actual trade execution (wrapped in a single DB transaction)
    # -------------------------------------------------------------------------

    # 3a. Compute fee
    fee_usd: float = size_usd * (fee_pct / 100.0)

    # 3b. Apply slippage to execution price
    if decision["signal"] == "BUY":
        execution_price: float = current_price * (1 + slippage_pct / 100.0)
    else:  # SELL
        execution_price = current_price * (1 - slippage_pct / 100.0)

    # Compute base asset size (e.g. BTC quantity)
    asset_size: float = size_usd / execution_price

    conn.execute("BEGIN")
    try:
        # 3c. Insert into decisions table (always log every acted-on decision)
        indicators_json = json.dumps(decision.get("indicators_snapshot", {}))
        cursor.execute(
            """
            INSERT INTO decisions (pair, signal, confidence, indicators_json, reasoning, acted_on)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                pair,
                decision["signal"],
                decision.get("confidence"),
                indicators_json,
                decision.get("reasoning", ""),
            ),
        )
        decision_id: int = cursor.lastrowid

        trade_id: Optional[int] = None

        if decision["signal"] == "BUY":
            # 3d-BUY. Open a new position then record the trade
            cursor.execute(
                """
                INSERT INTO positions (pair, side, status, entry_price, size, cost_basis_usd)
                VALUES (?, 'LONG', 'OPEN', ?, ?, ?)
                """,
                (pair, execution_price, asset_size, size_usd),
            )
            position_id: int = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee, slippage)
                VALUES (?, ?, ?, 'BUY', ?, ?, ?, ?)
                """,
                (position_id, decision_id, pair, execution_price, asset_size, fee_usd, slippage_pct),
            )
            trade_id = cursor.lastrowid

            # 3e. Update balance: available decreases by size_usd + fee, locked increases by size_usd
            new_locked = balance["locked_usd"] + size_usd
            new_available = balance["available_usd"] - size_usd - fee_usd
            new_total = balance["total_usd"] - fee_usd  # fee is a real cost
            Ledger.record_balance_snapshot(conn, new_total, new_available, new_locked)

        else:  # SELL
            # 3d-SELL. Find the oldest open LONG position for this pair to close
            cursor.execute(
                """
                SELECT id, entry_price, size, cost_basis_usd
                FROM positions
                WHERE pair = ? AND side = 'LONG' AND status = 'OPEN'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (pair,),
            )
            open_pos = cursor.fetchone()

            if open_pos is None:
                # No open position to close; reject cleanly
                conn.rollback()
                return {"executed": False, "reason": "no open position to SELL", "trade_id": None}

            position_id = open_pos["id"]
            cost_basis = open_pos["cost_basis_usd"]
            pos_asset_size = open_pos["size"]

            # Sell at execution_price for the position's full asset quantity
            proceeds_usd = pos_asset_size * execution_price
            realized_pnl = proceeds_usd - cost_basis - fee_usd

            cursor.execute(
                """
                UPDATE positions
                SET status = 'CLOSED',
                    closed_at = CURRENT_TIMESTAMP,
                    current_price = ?,
                    realized_pnl = ?
                WHERE id = ?
                """,
                (execution_price, realized_pnl, position_id),
            )

            cursor.execute(
                """
                INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee, slippage)
                VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?)
                """,
                (position_id, decision_id, pair, execution_price, pos_asset_size, fee_usd, slippage_pct),
            )
            trade_id = cursor.lastrowid

            # 3e. Update balance: release locked capital, credit proceeds minus fee
            # The locked capital from the BUY is now released; we receive proceeds
            released_locked = min(cost_basis, balance["locked_usd"])
            new_locked = max(0.0, balance["locked_usd"] - released_locked)
            new_available = balance["available_usd"] + proceeds_usd - fee_usd
            new_total = new_available + new_locked
            Ledger.record_balance_snapshot(conn, new_total, new_available, new_locked)

        # 3f. Commit the transaction
        conn.commit()
        return {"executed": True, "reason": "trade executed successfully", "trade_id": trade_id}

    except Exception as exc:
        conn.rollback()
        logger.error("Transaction rolled back due to error: %s", exc)
        return {"executed": False, "reason": f"transaction error: {exc}", "trade_id": None}
