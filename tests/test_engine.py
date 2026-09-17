"""
Unit tests for the rule-based decision engine:
- engine/indicators.py (compute_indicators)
- engine/rules.py (generate_signal)

All tests are strictly offline and DO NOT hit external networks.
"""
import numpy as np
import pandas as pd
import pytest

from engine.indicators import compute_indicators
from engine.rules import (
    generate_signal,
    MIN_AGREEING_INDICATORS,
    RSI_OVERSOLD_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
)


def _build_ohlcv(closes):
    """Helper to generate a minimal valid OHLCV DataFrame from a close sequence."""
    n = len(closes)
    return pd.DataFrame({
        "timestamp": [1700000000000 + i * 300000 for i in range(n)],
        "open": closes,
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })


# ==============================================================================
# 1. Indicators Tests (engine/indicators.py)
# ==============================================================================

def test_compute_indicators_added_columns():
    """Verify compute_indicators populates all required indicator columns with valid values on sufficient data."""
    # 60 candles is sufficient for EMA20, EMA50, RSI14, and MACD(12, 26, 9)
    closes = [100.0 + i for i in range(60)]
    df = _build_ohlcv(closes)
    
    result = compute_indicators(df)
    
    expected_cols = [
        "EMA_20", "EMA_50", "RSI_14", 
        "MACD", "MACD_signal", "MACD_hist"
    ]
    for col in expected_cols:
        assert col in result.columns, f"Missing required indicator column: {col}"
        # Latest row should have computed non-null float values
        latest_val = result[col].iloc[-1]
        assert pd.notna(latest_val), f"Column {col} has NaN in latest row with 60 candles"
        assert isinstance(latest_val, (float, np.floating))


def test_compute_indicators_insufficient_data_returns_nan_without_crashing():
    """Verify compute_indicators gracefully populates NaN for indicators when candles < lookback period."""
    # 25 candles: enough for RSI14 and EMA20, but not EMA50 or MACD
    closes = [100.0 + i for i in range(25)]
    df = _build_ohlcv(closes)
    
    result = compute_indicators(df)
    
    assert "EMA_50" in result.columns
    assert "MACD" in result.columns
    assert "MACD_hist" in result.columns
    
    # EMA50 and MACD require >= 35-50 candles, so all rows should be NaN
    assert result["EMA_50"].isna().all(), "EMA_50 must be all NaN for 25 candles"
    assert result["MACD_hist"].isna().all(), "MACD_hist must be all NaN for 25 candles"
    
    # EMA20 should have NaNs for the first 19 rows and a valid value at index 19-24
    assert result["EMA_20"].iloc[:19].isna().all()
    assert pd.notna(result["EMA_20"].iloc[-1])


def test_compute_indicators_missing_close_raises_value_error():
    """Verify compute_indicators raises a descriptive ValueError if 'close' column is missing."""
    df_invalid = pd.DataFrame({"open": [10.0, 11.0], "volume": [100, 200]})
    with pytest.raises(ValueError, match="DataFrame must contain 'close' column"):
        compute_indicators(df_invalid)


def test_compute_indicators_empty_df():
    """Verify compute_indicators returns empty DataFrame with expected columns if input has 0 rows."""
    df_empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    result = compute_indicators(df_empty)
    assert result.empty
    assert "EMA_20" in result.columns
    assert "MACD_hist" in result.columns


# ==============================================================================
# 2. Decision Rules Tests (engine/rules.py)
# ==============================================================================

def test_generate_signal_clear_uptrend_buy():
    """
    Clear uptrend test case:
    - EMA20 > EMA50 (bullish)
    - RSI neutral (55.0)
    - MACD histogram positive (+1.25) (bullish)
    Agreement: 2 of 3 bullish -> BUY signal.
    """
    df_uptrend = pd.DataFrame({
        "close": [150.0, 155.0],
        "EMA_20": [145.0, 152.0],
        "EMA_50": [140.0, 142.0],
        "RSI_14": [52.0, 55.0],
        "MACD": [2.5, 3.1],
        "MACD_signal": [1.5, 1.85],
        "MACD_hist": [1.0, 1.25],
    })
    
    decision = generate_signal(df_uptrend)
    
    assert decision["signal"] == "BUY"
    assert decision["confidence"] == pytest.approx(0.67, rel=1e-2)
    assert "EMA20 ($152.00) is above EMA50 ($142.00)" in decision["reasoning"]
    assert "RSI at 55.00 is neutral" in decision["reasoning"]
    assert "MACD histogram positive (1.2500)" in decision["reasoning"]
    assert "BUY signal generated" in decision["reasoning"]
    
    # Check snapshot dictionary
    snap = decision["indicators_snapshot"]
    assert snap["close"] == 155.0
    assert snap["ema_20"] == 152.0
    assert snap["ema_50"] == 142.0
    assert snap["rsi_14"] == 55.0
    assert snap["macd_hist"] == 1.25


def test_generate_signal_clear_downtrend_sell():
    """
    Clear downtrend test case:
    - EMA20 < EMA50 (bearish)
    - RSI neutral (42.0)
    - MACD histogram negative (-0.85) (bearish)
    Agreement: 2 of 3 bearish -> SELL signal.
    """
    df_downtrend = pd.DataFrame({
        "close": [95.0, 90.0],
        "EMA_20": [98.0, 92.0],
        "EMA_50": [105.0, 102.0],
        "RSI_14": [48.0, 42.0],
        "MACD": [-1.5, -2.2],
        "MACD_signal": [-0.8, -1.35],
        "MACD_hist": [-0.7, -0.85],
    })
    
    decision = generate_signal(df_downtrend)
    
    assert decision["signal"] == "SELL"
    assert decision["confidence"] == pytest.approx(0.67, rel=1e-2)
    assert "EMA20 ($92.00) is below EMA50 ($102.00)" in decision["reasoning"]
    assert "RSI at 42.00 is neutral" in decision["reasoning"]
    assert "MACD histogram negative (-0.8500)" in decision["reasoning"]
    assert "SELL signal generated" in decision["reasoning"]
    
    snap = decision["indicators_snapshot"]
    assert snap["close"] == 90.0
    assert snap["ema_20"] == 92.0
    assert snap["ema_50"] == 102.0
    assert snap["macd_hist"] == -0.85


def test_generate_signal_crossover_detection_in_reasoning():
    """Verify that a fresh crossover is explicitly identified in the reasoning."""
    # Row 0: EMA20 was below EMA50 (100 vs 105)
    # Row 1: EMA20 crosses above EMA50 (108 vs 106)
    df_cross = pd.DataFrame({
        "close": [102.0, 110.0],
        "EMA_20": [100.0, 108.0],
        "EMA_50": [105.0, 106.0],
        "RSI_14": [45.0, 58.0],
        "MACD_hist": [-0.2, 0.45],
    })
    
    decision = generate_signal(df_cross)
    
    assert decision["signal"] == "BUY"
    assert "EMA20 ($108.00) crossed above EMA50 ($106.00)" in decision["reasoning"]
    assert "RSI at 58.00 is neutral" in decision["reasoning"]
    assert "MACD histogram positive (0.4500)" in decision["reasoning"]


def test_generate_signal_unanimous_3_of_3_agreement():
    """Verify full confidence (1.0) when all 3 indicators agree."""
    # Oversold rebound: EMA20 > EMA50, RSI < 30 (oversold), MACD_hist > 0
    df_strong_buy = pd.DataFrame({
        "close": [120.0],
        "EMA_20": [125.0],
        "EMA_50": [115.0],
        "RSI_14": [28.5],
        "MACD_hist": [0.65],
    })
    
    decision = generate_signal(df_strong_buy)
    assert decision["signal"] == "BUY"
    assert decision["confidence"] == 1.0
    assert "RSI at 28.50 is oversold (<30)" in decision["reasoning"]
    assert "(3/3 indicators agree" in decision["reasoning"]


def test_generate_signal_mixed_conflicting_indicators_hold():
    """
    Conflicting indicators test case:
    - EMA20 > EMA50 (bullish: 1 vote)
    - MACD histogram negative (-0.4) (bearish: 1 vote)
    - RSI neutral (50.0: 0 vote)
    No direction achieves the threshold of 2 agreeing indicators -> HOLD.
    """
    df_conflicting = pd.DataFrame({
        "close": [100.0],
        "EMA_20": [105.0],
        "EMA_50": [95.0],
        "RSI_14": [50.0],
        "MACD_hist": [-0.4],
    })
    
    decision = generate_signal(df_conflicting)
    
    assert decision["signal"] == "HOLD"
    assert decision["confidence"] < 0.67
    assert "HOLD signal generated" in decision["reasoning"]
    assert "insufficient agreement" in decision["reasoning"]
    assert "1 buy, 1 sell vs threshold=2" in decision["reasoning"]


def test_generate_signal_insufficient_data_returns_hold_without_crash():
    """
    Insufficient data test case (< 50 candles):
    NaN indicator values must result in HOLD without throwing exceptions.
    """
    df_short = pd.DataFrame({
        "close": [100.0] * 10,
    })
    # Run through compute_indicators so NaN columns exist
    df_with_inds = compute_indicators(df_short)
    
    decision = generate_signal(df_with_inds)
    
    assert decision["signal"] == "HOLD"
    assert decision["confidence"] == 0.0
    assert "EMA20/EMA50 unavailable (insufficient data)" in decision["reasoning"]
    assert "RSI unavailable (insufficient data)" in decision["reasoning"]
    assert "MACD histogram unavailable (insufficient data)" in decision["reasoning"]
    assert "HOLD signal generated" in decision["reasoning"]
    
    snap = decision["indicators_snapshot"]
    assert snap["close"] == 100.0
    assert snap["ema_50"] is None
    assert snap["macd"] is None


def test_generate_signal_empty_dataframe():
    """Verify generate_signal handles an empty DataFrame safely."""
    df_empty = pd.DataFrame()
    decision = generate_signal(df_empty)
    
    assert decision["signal"] == "HOLD"
    assert decision["confidence"] == 0.0
    assert "empty" in decision["reasoning"].lower()
    assert decision["indicators_snapshot"]["close"] is None


# ==============================================================================
# 3. End-to-End Pipeline Tests (Compute Indicators -> Generate Signal)
# ==============================================================================

def test_full_pipeline_synthetic_accelerating_uptrend():
    """
    Full pipeline test:
    Construct 65 candles where price starts flat and then accelerates upwards.
    Indicators computed via compute_indicators() should produce a clear BUY signal.
    """
    # 35 flat candles followed by 30 accelerating upward candles
    closes = [100.0] * 35 + [100.0 + (i ** 1.6) for i in range(1, 31)]
    df = _build_ohlcv(closes)
    
    df_inds = compute_indicators(df)
    decision = generate_signal(df_inds)
    
    assert decision["signal"] == "BUY"
    assert decision["confidence"] >= 0.67
    assert decision["indicators_snapshot"]["ema_20"] > decision["indicators_snapshot"]["ema_50"]
    assert decision["indicators_snapshot"]["macd_hist"] > 0


def test_full_pipeline_synthetic_accelerating_downtrend():
    """
    Full pipeline test:
    Construct 65 candles where price starts flat and then accelerates downwards.
    Indicators computed via compute_indicators() should produce a clear SELL signal.
    """
    # 35 flat candles at 200.0 followed by 30 accelerating downward candles
    closes = [200.0] * 35 + [200.0 - (i ** 1.6) for i in range(1, 31)]
    df = _build_ohlcv(closes)
    
    df_inds = compute_indicators(df)
    decision = generate_signal(df_inds)
    
    assert decision["signal"] == "SELL"
    assert decision["confidence"] >= 0.67
    assert decision["indicators_snapshot"]["ema_20"] < decision["indicators_snapshot"]["ema_50"]
    assert decision["indicators_snapshot"]["macd_hist"] < 0

