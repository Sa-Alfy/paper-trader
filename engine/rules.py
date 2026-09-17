"""
Rule-based decision engine for paper trader.
Evaluates technical indicators (EMA crossover, RSI confirmation, MACD momentum)
and generates BUY, SELL, or HOLD signals with confidence and explainable reasoning.
"""
import logging
from typing import Any, Dict, Optional
import pandas as pd

logger = logging.getLogger(__name__)

# ==============================================================================
# Strategy Thresholds and Parameters (Easily tunable configuration)
# ==============================================================================
# Minimum number of agreeing indicators required out of 3 (EMA, RSI, MACD)
# to generate a directional signal (BUY or SELL). If fewer agree, engine returns HOLD.
MIN_AGREEING_INDICATORS: int = 2
TOTAL_INDICATORS: int = 3

# RSI thresholds for overbought and oversold conditions
RSI_OVERSOLD_THRESHOLD: float = 30.0    # RSI < 30 indicates oversold (bullish confirmation)
RSI_OVERBOUGHT_THRESHOLD: float = 70.0  # RSI > 70 indicates overbought (bearish confirmation)


def _safe_float(val: Any) -> Optional[float]:
    """Converts a scalar to a standard Python float if valid and not NaN; returns None otherwise."""
    if val is None or pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_indicator_val(row: pd.Series, *keys: str) -> Optional[float]:
    """Helper to extract an indicator value from a Series checking multiple possible aliases."""
    for key in keys:
        if key in row:
            val = _safe_float(row[key])
            if val is not None:
                return val
    return None


def generate_signal(
    df_with_indicators: pd.DataFrame,
    min_agreement: int = MIN_AGREEING_INDICATORS,
    rsi_oversold: float = RSI_OVERSOLD_THRESHOLD,
    rsi_overbought: float = RSI_OVERBOUGHT_THRESHOLD,
) -> Dict[str, Any]:
    """
    Evaluates the latest candle's technical indicators and produces a trading decision.

    Strategy Logic:
        1. Trend Direction (EMA):
           - Bullish (BUY): EMA20 > EMA50 (with crossover detected if previous EMA20 <= EMA50)
           - Bearish (SELL): EMA20 < EMA50 (with crossover detected if previous EMA20 >= EMA50)
        2. Momentum Confirmation (RSI):
           - Bullish confirmation (BUY): RSI < 30 (oversold)
           - Bearish confirmation (SELL): RSI > 70 (overbought)
           - Neutral (HOLD): 30 <= RSI <= 70
        3. Momentum Confirmation (MACD Histogram):
           - Bullish momentum (BUY): MACD histogram > 0
           - Bearish momentum (SELL): MACD histogram < 0
           - Neutral (HOLD): MACD histogram == 0

    Threshold Rule:
        At least `min_agreement` (default: 2) out of 3 indicators must agree on BUY or SELL.
        Otherwise, the engine emits HOLD.

    Returns:
        dict: {
            "signal": "BUY" | "SELL" | "HOLD",
            "confidence": float (0.0 to 1.0),
            "reasoning": str (plain-language explanation referencing actual indicator values),
            "indicators_snapshot": dict (raw indicator values for decisions.indicators_json)
        }
    """
    empty_snapshot = {
        "close": None,
        "ema_20": None,
        "ema_50": None,
        "rsi_14": None,
        "macd": None,
        "macd_signal": None,
        "macd_hist": None,
    }

    if df_with_indicators is None or df_with_indicators.empty:
        return {
            "signal": "HOLD",
            "confidence": 0.0,
            "reasoning": "Insufficient data: DataFrame is empty, cannot compute signals.",
            "indicators_snapshot": empty_snapshot,
        }

    curr_row = df_with_indicators.iloc[-1]
    prev_row = df_with_indicators.iloc[-2] if len(df_with_indicators) >= 2 else None

    # Extract indicator values with fallback aliases
    close = _extract_indicator_val(curr_row, "close", "Close")
    ema20 = _extract_indicator_val(curr_row, "EMA_20", "EMA20", "ema_20", "ema20")
    ema50 = _extract_indicator_val(curr_row, "EMA_50", "EMA50", "ema_50", "ema50")
    rsi = _extract_indicator_val(curr_row, "RSI_14", "RSI", "rsi_14", "rsi")
    macd = _extract_indicator_val(curr_row, "MACD", "MACD_12_26_9", "macd")
    macd_signal = _extract_indicator_val(curr_row, "MACD_signal", "MACDs_12_26_9", "macd_signal")
    macd_hist = _extract_indicator_val(curr_row, "MACD_hist", "MACDh_12_26_9", "macd_hist")

    snapshot = {
        "close": close,
        "ema_20": ema20,
        "ema_50": ema50,
        "rsi_14": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
    }

    # 1. Evaluate EMA Trend & Crossover
    prev_ema20 = _extract_indicator_val(prev_row, "EMA_20", "EMA20", "ema_20", "ema20") if prev_row is not None else None
    prev_ema50 = _extract_indicator_val(prev_row, "EMA_50", "EMA50", "ema_50", "ema50") if prev_row is not None else None

    ema_vote = "NEUTRAL"
    if ema20 is not None and ema50 is not None:
        if ema20 > ema50:
            ema_vote = "BUY"
            if prev_ema20 is not None and prev_ema50 is not None and prev_ema20 <= prev_ema50:
                ema_reason = f"EMA20 (${ema20:.2f}) crossed above EMA50 (${ema50:.2f})"
            else:
                ema_reason = f"EMA20 (${ema20:.2f}) is above EMA50 (${ema50:.2f})"
        elif ema20 < ema50:
            ema_vote = "SELL"
            if prev_ema20 is not None and prev_ema50 is not None and prev_ema20 >= prev_ema50:
                ema_reason = f"EMA20 (${ema20:.2f}) crossed below EMA50 (${ema50:.2f})"
            else:
                ema_reason = f"EMA20 (${ema20:.2f}) is below EMA50 (${ema50:.2f})"
        else:
            ema_reason = f"EMA20 (${ema20:.2f}) equals EMA50 (${ema50:.2f})"
    else:
        ema_reason = "EMA20/EMA50 unavailable (insufficient data)"

    # 2. Evaluate RSI Confirmation
    rsi_vote = "NEUTRAL"
    if rsi is not None:
        if rsi < rsi_oversold:
            rsi_vote = "BUY"
            rsi_reason = f"RSI at {rsi:.2f} is oversold (<{rsi_oversold:.0f})"
        elif rsi > rsi_overbought:
            rsi_vote = "SELL"
            rsi_reason = f"RSI at {rsi:.2f} is overbought (>{rsi_overbought:.0f})"
        else:
            rsi_vote = "NEUTRAL"
            rsi_reason = f"RSI at {rsi:.2f} is neutral"
    else:
        rsi_reason = "RSI unavailable (insufficient data)"

    # 3. Evaluate MACD Histogram Momentum
    macd_vote = "NEUTRAL"
    if macd_hist is not None:
        if macd_hist > 0:
            macd_vote = "BUY"
            macd_reason = f"MACD histogram positive ({macd_hist:.4f})"
        elif macd_hist < 0:
            macd_vote = "SELL"
            macd_reason = f"MACD histogram negative ({macd_hist:.4f})"
        else:
            macd_vote = "NEUTRAL"
            macd_reason = f"MACD histogram zero ({macd_hist:.4f})"
    else:
        macd_reason = "MACD histogram unavailable (insufficient data)"

    # 4. Tally votes and determine final signal
    votes = [ema_vote, rsi_vote, macd_vote]
    buy_count = votes.count("BUY")
    sell_count = votes.count("SELL")

    breakdown = f"{ema_reason}, {rsi_reason}, {macd_reason}"

    if buy_count >= min_agreement:
        signal = "BUY"
        confidence = round(buy_count / TOTAL_INDICATORS, 2)
        reasoning = f"{breakdown} -> BUY signal generated ({buy_count}/{TOTAL_INDICATORS} indicators agree, threshold={min_agreement})"
    elif sell_count >= min_agreement:
        signal = "SELL"
        confidence = round(sell_count / TOTAL_INDICATORS, 2)
        reasoning = f"{breakdown} -> SELL signal generated ({sell_count}/{TOTAL_INDICATORS} indicators agree, threshold={min_agreement})"
    else:
        signal = "HOLD"
        max_agree = max(buy_count, sell_count)
        confidence = round(max_agree / TOTAL_INDICATORS, 2)
        reasoning = f"{breakdown} -> HOLD signal generated (insufficient agreement: {buy_count} buy, {sell_count} sell vs threshold={min_agreement})"

    return {
        "signal": signal,
        "confidence": confidence,
        "reasoning": reasoning,
        "indicators_snapshot": snapshot,
    }

