"""
Failure taxonomy (6 patterns): MACD_FALSE_POSITIVE, RSI_FAKE_REVERSAL,
INSUFFICIENT_AGREEMENT, OVERSIZED_ENTRY_HIGH_VOLATILITY, WHIPSAW_EMA, MISSED_OPPORTUNITY.
Lookback window for missed-opportunity is config-driven (evaluator.missed_opportunity_lookback_days).
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config.config import load_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Failure pattern constants
# ---------------------------------------------------------------------------
PATTERN_MACD_FALSE_POSITIVE = "MACD_FALSE_POSITIVE"
PATTERN_RSI_FAKE_REVERSAL = "RSI_FAKE_REVERSAL"
PATTERN_INSUFFICIENT_AGREEMENT = "INSUFFICIENT_AGREEMENT"
PATTERN_OVERSIZED_ENTRY_HIGH_VOL = "OVERSIZED_ENTRY_HIGH_VOLATILITY"
PATTERN_WHIPSAW_EMA = "WHIPSAW_EMA"
PATTERN_MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_indicators(indicators_json: Optional[str]) -> Dict[str, Any]:
    if not indicators_json:
        return {}
    try:
        return json.loads(indicators_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_lookback_days(settings: Dict[str, Any]) -> int:
    return int(settings.get("evaluator", {}).get("missed_opportunity_lookback_days", 3))


def _classify_pattern(
    signal: str,
    indicators: Dict[str, Any],
    realized_pnl: Optional[float],
    is_acted_on: bool,
) -> Optional[str]:
    """
    Classify a single acted-on trade into a failure pattern if applicable.
    Returns None when no failure pattern applies (i.e., a clean win).
    """
    if not is_acted_on or realized_pnl is None:
        return None

    macd_hist = indicators.get("macd_hist")
    rsi = indicators.get("rsi_14")
    ema20 = indicators.get("ema_20")
    ema50 = indicators.get("ema_50")

    # Profitable trades: check for OVERSIZED_ENTRY_HIGH_VOL
    # (no realized_pnl threshold — pattern is based on indicator state alone)
    pnl_is_loss = realized_pnl < 0.0

    if not pnl_is_loss:
        return None  # clean win — no failure pattern

    # Loss trade: determine which indicator(s) were at fault

    # MACD_FALSE_POSITIVE: MACD signalled the trade direction but price reversed
    if macd_hist is not None:
        if signal == "BUY" and macd_hist > 0:
            # RSI was neutral or missing — MACD was the lone driver
            if rsi is None or 30.0 <= rsi <= 70.0:
                return PATTERN_MACD_FALSE_POSITIVE

        if signal == "SELL" and macd_hist < 0:
            if rsi is None or 30.0 <= rsi <= 70.0:
                return PATTERN_MACD_FALSE_POSITIVE

    # RSI_FAKE_REVERSAL: RSI triggered but price didn't actually reverse
    if rsi is not None:
        if signal == "BUY" and rsi < 30.0:
            return PATTERN_RSI_FAKE_REVERSAL
        if signal == "SELL" and rsi > 70.0:
            return PATTERN_RSI_FAKE_REVERSAL

    # WHIPSAW_EMA: EMA alignment existed but trade was short-lived loss
    if ema20 is not None and ema50 is not None:
        if signal == "BUY" and ema20 > ema50:
            return PATTERN_WHIPSAW_EMA
        if signal == "SELL" and ema20 < ema50:
            return PATTERN_WHIPSAW_EMA

    # Fallback: generic loss without clear indicator culprit
    return PATTERN_INSUFFICIENT_AGREEMENT


def _classify_high_vol_pattern(
    indicators: Dict[str, Any],
    size_usd: float,
    settings: Dict[str, Any],
) -> Optional[str]:
    """
    Flag OVERSIZED_ENTRY_HIGH_VOL when entry size is at/near cap during high MACD volatility.
    'High volatility' proxy: abs(macd_hist) > 0.5% of close price.
    """
    macd_hist = indicators.get("macd_hist")
    close = indicators.get("close")
    max_pos = settings.get("risk_limits", {}).get("max_position_size_usd", 1000.0)

    if macd_hist is None or close is None or close == 0.0:
        return None

    volatility_ratio = abs(macd_hist) / abs(close)
    is_high_vol = volatility_ratio > 0.005  # > 0.5% of price

    # "Near cap" = used >= 80% of the max position limit
    at_cap = size_usd >= max_pos * 0.80

    if is_high_vol and at_cap:
        return PATTERN_OVERSIZED_ENTRY_HIGH_VOL
    return None


def _fetch_closed_trades_with_decisions(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """
    Returns all closed-position SELL trades joined to their originating BUY decision.
    The FK on trades.decision_id is used to get the decision that triggered each trade.
    We also need the realized_pnl from positions.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            t.id          AS trade_id,
            t.decision_id,
            t.pair,
            t.side,
            t.price       AS trade_price,
            t.size        AS trade_size,
            t.fee,
            t.slippage,
            t.executed_at,
            p.realized_pnl,
            p.cost_basis_usd,
            p.entry_price,
            p.closed_at,
            d.signal,
            d.confidence,
            d.indicators_json,
            d.reasoning,
            d.acted_on,
            d.timestamp   AS decision_timestamp
        FROM trades t
        JOIN positions p ON t.position_id = p.id
        JOIN decisions d ON t.decision_id = d.id
        WHERE p.status = 'CLOSED'
          AND t.side = 'SELL'
        ORDER BY t.executed_at ASC
        """
    )
    return cur.fetchall()


def _fetch_unevaluated_decisions(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """
    Returns decisions that were NOT acted on (rejected or HOLD) so we can check
    whether they represent a missed opportunity.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            d.id,
            d.timestamp,
            d.pair,
            d.signal,
            d.confidence,
            d.indicators_json,
            d.reasoning,
            d.acted_on,
            d.rejection_reason
        FROM decisions d
        WHERE d.acted_on = 0
        ORDER BY d.timestamp ASC
        """
    )
    return cur.fetchall()


def _get_price_after(
    conn: sqlite3.Connection,
    pair: str,
    after_ts: str,
    before_ts: str,
) -> Optional[float]:
    """
    Returns the first known trade price for `pair` in the window (after_ts, before_ts].
    Uses only stored data — no network calls.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.price
        FROM trades t
        JOIN positions p ON t.position_id = p.id
        WHERE t.pair = ?
          AND t.side = 'SELL'
          AND t.executed_at > ?
          AND t.executed_at <= ?
        ORDER BY t.executed_at ASC
        LIMIT 1
        """,
        (pair, after_ts, before_ts),
    )
    row = cur.fetchone()
    return float(row["price"]) if row else None


def _check_missed_opportunity(
    decision_row: sqlite3.Row,
    conn: sqlite3.Connection,
    lookback_days: int,
    fee_pct: float,
) -> bool:
    """
    For a rejected/HOLD decision, check if executing would have been profitable
    within the lookback window using only stored price data.
    Returns True if it was a missed opportunity.
    """
    signal = decision_row["signal"]
    if signal == "HOLD":
        # HOLD decisions have no directional intent — skip
        return False

    decision_ts = decision_row["timestamp"]
    pair = decision_row["pair"]

    # Parse decision timestamp
    try:
        dt_decision = datetime.fromisoformat(decision_ts)
    except (ValueError, TypeError):
        return False

    dt_window_end = dt_decision + timedelta(days=lookback_days)
    window_end_str = dt_window_end.isoformat()

    indicators = _parse_indicators(decision_row["indicators_json"])
    entry_price_approx = indicators.get("close")
    if not entry_price_approx or entry_price_approx <= 0.0:
        return False

    exit_price = _get_price_after(conn, pair, decision_ts, window_end_str)
    if exit_price is None:
        return False

    # Simulate simple round-trip PnL (no slippage, just fee)
    fee_factor = 1.0 - (fee_pct / 100.0)
    if signal == "BUY":
        simulated_pnl = (exit_price - entry_price_approx) * fee_factor
    else:  # SELL (short)
        simulated_pnl = (entry_price_approx - exit_price) * fee_factor

    return simulated_pnl > 0.0


def _insert_evaluation(
    conn: sqlite3.Connection,
    start_ts: str,
    end_ts: str,
    total_trades: int,
    win_rate: float,
    total_pnl: float,
    max_drawdown: float,
    sharpe_ratio: Optional[float],
    mistakes: List[str],
    behavior_tags: List[str],
    notes: str,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO evaluations
            (start_timestamp, end_timestamp, total_trades, win_rate, total_pnl,
             max_drawdown, sharpe_ratio, mistakes_json, behavior_tags_json, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            start_ts,
            end_ts,
            total_trades,
            win_rate,
            total_pnl,
            max_drawdown,
            sharpe_ratio,
            json.dumps(mistakes),
            json.dumps(behavior_tags),
            notes,
        ),
    )
    return cur.lastrowid


def _compute_max_drawdown(pnl_list: List[float]) -> float:
    """Peak-to-trough max drawdown over the cumulative PnL series."""
    if not pnl_list:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    cumulative = 0.0
    for pnl in pnl_list:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_sharpe(pnl_list: List[float]) -> Optional[float]:
    """Annualised Sharpe using daily PnL series (RF=0). Returns None if std==0."""
    if len(pnl_list) < 2:
        return None
    n = len(pnl_list)
    mean = sum(pnl_list) / n
    variance = sum((x - mean) ** 2 for x in pnl_list) / (n - 1)
    if variance == 0.0:
        return None
    std = variance ** 0.5
    return round((mean / std) * (252 ** 0.5), 4)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate_decisions(conn: sqlite3.Connection) -> str:
    """
    Evaluates all closed trades and unevaluated rejected/HOLD decisions.
    Writes one evaluation row per run to the evaluations table.
    Returns a plain-English summary string and also prints it.
    """
    settings = load_settings()
    fee_pct: float = settings["simulation"]["fee_pct"]
    lookback_days: int = _get_lookback_days(settings)

    # ------------------------------------------------------------------ #
    # 1. Closed trades — attribute P&L to indicators
    # ------------------------------------------------------------------ #
    closed_trades = _fetch_closed_trades_with_decisions(conn)

    pnl_list: List[float] = []
    mistakes: List[str] = []
    behavior_tags: List[str] = []
    wins = 0
    total = len(closed_trades)

    for row in closed_trades:
        indicators = _parse_indicators(row["indicators_json"])
        pnl = float(row["realized_pnl"]) if row["realized_pnl"] is not None else 0.0
        pnl_list.append(pnl)

        if pnl > 0:
            wins += 1

        # Check for oversized-entry-in-high-volatility regardless of outcome
        cost_basis = float(row["cost_basis_usd"]) if row["cost_basis_usd"] is not None else 0.0
        hv_pattern = _classify_high_vol_pattern(indicators, cost_basis, settings)
        if hv_pattern:
            mistakes.append(hv_pattern)
            behavior_tags.append("high_vol_entry")

        # Classify loss patterns
        pattern = _classify_pattern(
            signal=row["signal"],
            indicators=indicators,
            realized_pnl=pnl,
            is_acted_on=bool(row["acted_on"]),
        )
        if pattern:
            mistakes.append(pattern)

    # ------------------------------------------------------------------ #
    # 2. Rejected / HOLD decisions — missed opportunity check
    # ------------------------------------------------------------------ #
    unevaluated = _fetch_unevaluated_decisions(conn)
    missed_count = 0

    for row in unevaluated:
        is_missed = _check_missed_opportunity(row, conn, lookback_days, fee_pct)
        if is_missed:
            missed_count += 1
            mistakes.append(PATTERN_MISSED_OPPORTUNITY)
            behavior_tags.append("missed_opportunity")

    # ------------------------------------------------------------------ #
    # 3. Aggregate metrics
    # ------------------------------------------------------------------ #
    total_pnl: float = sum(pnl_list)
    win_rate: float = (wins / total) if total > 0 else 0.0
    max_drawdown: float = _compute_max_drawdown(pnl_list)
    sharpe: Optional[float] = _compute_sharpe(pnl_list)

    # Timestamps: span of evaluated decisions/trades
    all_timestamps: List[str] = []
    for row in closed_trades:
        if row["executed_at"]:
            all_timestamps.append(row["executed_at"])
    for row in unevaluated:
        if row["timestamp"]:
            all_timestamps.append(row["timestamp"])

    now_str = datetime.utcnow().isoformat()
    start_ts = min(all_timestamps) if all_timestamps else now_str
    end_ts = max(all_timestamps) if all_timestamps else now_str

    # Unique mistake counts for notes
    from collections import Counter
    mistake_counts = Counter(mistakes)

    notes_parts = [
        f"Evaluated {total} closed trade(s) + {len(unevaluated)} rejected/HOLD decision(s).",
        f"Missed opportunities detected: {missed_count} (lookback={lookback_days}d).",
    ]
    if mistake_counts:
        top = mistake_counts.most_common(3)
        notes_parts.append("Top patterns: " + ", ".join(f"{p}×{c}" for p, c in top) + ".")
    notes = " ".join(notes_parts)

    # ------------------------------------------------------------------ #
    # 4. Write evaluation row
    # ------------------------------------------------------------------ #
    with conn:
        eval_id = _insert_evaluation(
            conn=conn,
            start_ts=start_ts,
            end_ts=end_ts,
            total_trades=total,
            win_rate=round(win_rate, 4),
            total_pnl=round(total_pnl, 6),
            max_drawdown=round(max_drawdown, 6),
            sharpe_ratio=sharpe,
            mistakes=list(mistake_counts.keys()),
            behavior_tags=list(set(behavior_tags)),
            notes=notes,
        )

    # ------------------------------------------------------------------ #
    # 5. Plain-English summary
    # ------------------------------------------------------------------ #
    summary_lines = [
        "=== Evaluator Report ===",
        f"Closed trades evaluated : {total}",
        f"Win rate                : {win_rate:.1%}",
        f"Total realized P&L      : {total_pnl:+.4f} USD",
        f"Max drawdown            : {max_drawdown:.4f} USD",
        f"Sharpe ratio            : {sharpe if sharpe is not None else 'N/A'}",
        f"Missed opportunities    : {missed_count} (lookback {lookback_days}d)",
        f"Failure patterns found  : {', '.join(mistake_counts.keys()) or 'None'}",
        f"Evaluation row written  : id={eval_id}",
    ]
    summary = "\n".join(summary_lines)
    print(summary)
    return summary
