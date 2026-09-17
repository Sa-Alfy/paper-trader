"""
Technical indicator computation using pandas-ta.
Adds EMA(20), EMA(50), RSI(14), and MACD(12, 26, 9) to OHLCV DataFrames.
"""
from typing import Optional
import numpy as np
import pandas as pd
import pandas_ta as ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes technical indicators for a given OHLCV DataFrame using pandas-ta.
    
    Required input columns:
        - 'close': closing prices (numeric)
        
    Added columns:
        - 'EMA_20': Exponential Moving Average (20 periods)
        - 'EMA_50': Exponential Moving Average (50 periods)
        - 'RSI_14': Relative Strength Index (14 periods)
        - 'MACD': MACD line (fast=12, slow=26, signal=9)
        - 'MACD_signal': MACD signal line (9 periods)
        - 'MACD_hist': MACD histogram
        - Along with pandas-ta default column names for compatibility:
          'MACD_12_26_9', 'MACDs_12_26_9', 'MACDh_12_26_9'
          
    Gracefully handles insufficient rows (< 50 candles) by populating NaN
    for indicators whose lookback period exceeds the available data.
    """
    if df is None:
        raise ValueError("Input DataFrame cannot be None")
    
    result = df.copy()
    
    if "close" not in result.columns:
        raise ValueError("DataFrame must contain 'close' column to compute indicators")
    
    if result.empty:
        for col in [
            "EMA_20", "EMA_50", "RSI_14", 
            "MACD", "MACD_signal", "MACD_hist",
            "MACD_12_26_9", "MACDs_12_26_9", "MACDh_12_26_9"
        ]:
            result[col] = pd.Series(dtype=float)
        return result
        
    # Ensure close column is numeric float
    close_series = pd.to_numeric(result["close"], errors="coerce")
    
    # EMA 20
    ema20 = ta.ema(close_series, length=20)
    result["EMA_20"] = ema20 if ema20 is not None else pd.Series(np.nan, index=result.index, dtype=float)
    
    # EMA 50
    ema50 = ta.ema(close_series, length=50)
    result["EMA_50"] = ema50 if ema50 is not None else pd.Series(np.nan, index=result.index, dtype=float)
    
    # RSI 14
    rsi14 = ta.rsi(close_series, length=14)
    result["RSI_14"] = rsi14 if rsi14 is not None else pd.Series(np.nan, index=result.index, dtype=float)
    
    # MACD (12, 26, 9)
    macd_df = ta.macd(close_series, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        result["MACD"] = macd_df["MACD_12_26_9"]
        result["MACD_signal"] = macd_df["MACDs_12_26_9"]
        result["MACD_hist"] = macd_df["MACDh_12_26_9"]
        
        result["MACD_12_26_9"] = macd_df["MACD_12_26_9"]
        result["MACDs_12_26_9"] = macd_df["MACDs_12_26_9"]
        result["MACDh_12_26_9"] = macd_df["MACDh_12_26_9"]
    else:
        result["MACD"] = pd.Series(np.nan, index=result.index, dtype=float)
        result["MACD_signal"] = pd.Series(np.nan, index=result.index, dtype=float)
        result["MACD_hist"] = pd.Series(np.nan, index=result.index, dtype=float)
        
        result["MACD_12_26_9"] = pd.Series(np.nan, index=result.index, dtype=float)
        result["MACDs_12_26_9"] = pd.Series(np.nan, index=result.index, dtype=float)
        result["MACDh_12_26_9"] = pd.Series(np.nan, index=result.index, dtype=float)
        
    return result

