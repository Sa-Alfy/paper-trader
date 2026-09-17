import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

import ccxt
import pandas as pd

from config.config import load_settings

logger = logging.getLogger(__name__)

# Default public exchange client (rate-limited, no API credentials)
_default_exchange: Optional[ccxt.binance] = None


class BinanceClientError(Exception):
    """Base exception for Binance client operations."""
    pass


class BinanceNetworkError(BinanceClientError):
    """Raised on connection timeout or network disruption."""
    pass


class BinanceRateLimitError(BinanceClientError):
    """Raised when Binance rate limit is exceeded."""
    pass


class BinanceExchangeError(BinanceClientError):
    """Raised when Binance returns an exchange error (e.g., unknown symbol)."""
    pass


def get_exchange_client() -> ccxt.binance:
    """
    Returns a singleton public ccxt.binance client with rate-limiting enabled.
    Strictly unauthenticated: no API key or secret is passed.
    """
    global _default_exchange
    if _default_exchange is None:
        _default_exchange = ccxt.binance({
            "enableRateLimit": True,
            "timeout": 15000,
        })
    return _default_exchange


def _resolve_pair(pair: Optional[str] = None) -> str:
    """
    Resolves trading pair from argument or fallback to config/settings.yaml.
    Never hardcodes 'BTC/USDT'.
    """
    if pair is not None and pair.strip():
        return pair.strip()
    
    settings = load_settings()
    configured_symbol = settings.get("trading", {}).get("symbol")
    if not configured_symbol:
        raise BinanceClientError("No trading pair provided and none found in config/settings.yaml")
    return configured_symbol


def _resolve_timeframe(timeframe: Optional[str] = None) -> str:
    """
    Resolves timeframe from argument or fallback to config/settings.yaml.
    Never hardcodes a default timeframe.
    """
    if timeframe is not None and timeframe.strip():
        return timeframe.strip()
    
    settings = load_settings()
    configured_timeframe = settings.get("trading", {}).get("timeframe")
    if not configured_timeframe:
        raise BinanceClientError("No timeframe provided and none found in config/settings.yaml")
    return configured_timeframe


def fetch_current_price(
    pair: Optional[str] = None,
    exchange: Optional[ccxt.binance] = None
) -> float:
    """
    Fetches the current market price for a given pair from Binance public API.
    If pair is omitted, reads the configured pair from config/settings.yaml.
    
    Returns:
        float: Latest traded price.
    Raises:
        BinanceNetworkError, BinanceRateLimitError, BinanceExchangeError, BinanceClientError
    """
    target_pair = _resolve_pair(pair)
    client = exchange or get_exchange_client()
    action = f"fetch_current_price for {target_pair}"
    
    logger.info("Attempting %s", action)
    try:
        ticker = client.fetch_ticker(target_pair)
        price = ticker.get("last")
        if price is None or price <= 0:
            raise BinanceClientError(f"Received invalid price ({price}) for {target_pair}")
        return float(price)
    except ccxt.RateLimitExceeded as e:
        logger.error("Rate limit exceeded during %s: %s", action, e)
        raise BinanceRateLimitError(f"Rate limit exceeded during {action}: {e}") from e
    except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
        logger.error("Network failure during %s: %s", action, e)
        raise BinanceNetworkError(f"Network failure during {action}: {e}") from e
    except ccxt.ExchangeError as e:
        logger.error("Exchange error during %s: %s", action, e)
        raise BinanceExchangeError(f"Exchange error during {action}: {e}") from e
    except Exception as e:
        logger.error("Unexpected error during %s: %s", action, e)
        raise BinanceClientError(f"Failed to execute {action}: {e}") from e


def fetch_ohlcv(
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: int = 100,
    as_datetime: bool = False,
    exchange: Optional[ccxt.binance] = None
) -> pd.DataFrame:
    """
    Fetches historical OHLCV candles from Binance public API.
    If pair or timeframe is omitted, reads them from config/settings.yaml.
    
    Returns:
        pandas.DataFrame: DataFrame with columns:
            ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            Timestamps are in ascending order.
    Raises:
        BinanceNetworkError, BinanceRateLimitError, BinanceExchangeError, BinanceClientError
    """
    target_pair = _resolve_pair(pair)
    target_timeframe = _resolve_timeframe(timeframe)
    client = exchange or get_exchange_client()
    action = f"fetch_ohlcv for {target_pair} (timeframe={target_timeframe}, limit={limit})"
    
    logger.info("Attempting %s", action)
    try:
        raw_ohlcv = client.fetch_ohlcv(target_pair, timeframe=target_timeframe, limit=limit)
        if not raw_ohlcv:
            raise BinanceClientError(f"Empty OHLCV data returned for {target_pair}")
        
        columns = ["timestamp", "open", "high", "low", "close", "volume"]
        df = pd.DataFrame(raw_ohlcv, columns=columns)
        
        # Ensure proper numeric typing
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        
        if as_datetime:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        else:
            df["timestamp"] = df["timestamp"].astype("int64")
            
        return df
    except ccxt.RateLimitExceeded as e:
        logger.error("Rate limit exceeded during %s: %s", action, e)
        raise BinanceRateLimitError(f"Rate limit exceeded during {action}: {e}") from e
    except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
        logger.error("Network failure during %s: %s", action, e)
        raise BinanceNetworkError(f"Network failure during {action}: {e}") from e
    except ccxt.ExchangeError as e:
        logger.error("Exchange error during %s: %s", action, e)
        raise BinanceExchangeError(f"Exchange error during {action}: {e}") from e
    except Exception as e:
        logger.error("Unexpected error during %s: %s", action, e)
        raise BinanceClientError(f"Failed to execute {action}: {e}") from e


def fetch_order_book(
    pair: Optional[str] = None,
    depth: int = 20,
    exchange: Optional[ccxt.binance] = None
) -> Dict[str, Any]:
    """
    Fetches the current L2 order book (bids and asks) from Binance public API.
    If pair is omitted, reads the configured pair from config/settings.yaml.
    
    Returns:
        dict: {
            'bids': [[price, amount], ...],
            'asks': [[price, amount], ...],
            'timestamp': int,
            'datetime': str
        }
    Raises:
        BinanceNetworkError, BinanceRateLimitError, BinanceExchangeError, BinanceClientError
    """
    target_pair = _resolve_pair(pair)
    client = exchange or get_exchange_client()
    action = f"fetch_order_book for {target_pair} (depth={depth})"
    
    logger.info("Attempting %s", action)
    try:
        raw_book = client.fetch_order_book(target_pair, limit=depth)
        bids = raw_book.get("bids", [])[:depth]
        asks = raw_book.get("asks", [])[:depth]
        
        if not bids and not asks:
            raise BinanceClientError(f"Empty order book received for {target_pair}")
            
        return {
            "bids": bids,
            "asks": asks,
            "timestamp": raw_book.get("timestamp"),
            "datetime": raw_book.get("datetime"),
        }
    except ccxt.RateLimitExceeded as e:
        logger.error("Rate limit exceeded during %s: %s", action, e)
        raise BinanceRateLimitError(f"Rate limit exceeded during {action}: {e}") from e
    except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
        logger.error("Network failure during %s: %s", action, e)
        raise BinanceNetworkError(f"Network failure during {action}: {e}") from e
    except ccxt.ExchangeError as e:
        logger.error("Exchange error during %s: %s", action, e)
        raise BinanceExchangeError(f"Exchange error during {action}: {e}") from e
    except Exception as e:
        logger.error("Unexpected error during %s: %s", action, e)
        raise BinanceClientError(f"Failed to execute {action}: {e}") from e

