import pytest
import pandas as pd
from data.binance_client import (
    fetch_current_price,
    fetch_ohlcv,
    fetch_order_book,
    BinanceClientError,
)
from config.config import load_settings


pytestmark = pytest.mark.network


def test_fetch_current_price_live():
    """
    NOTE: Requires active internet access to Binance public REST API.
    Confirms fetch_current_price returns a positive float for the configured pair.
    """
    settings = load_settings()
    configured_pair = settings["trading"]["symbol"]
    
    price = fetch_current_price(pair=configured_pair)
    assert isinstance(price, float), f"Expected float price, got {type(price)}"
    assert price > 0.0, f"Expected positive price, got {price}"


def test_fetch_current_price_config_fallback():
    """
    NOTE: Requires active internet access to Binance public REST API.
    Confirms fetch_current_price correctly reads pair from config/settings.yaml when omitted.
    """
    price = fetch_current_price()
    assert isinstance(price, float)
    assert price > 0.0


def test_fetch_ohlcv_live():
    """
    NOTE: Requires active internet access to Binance public REST API.
    Confirms fetch_ohlcv returns a non-empty DataFrame with proper columns:
    ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    and timestamps in strictly ascending order.
    """
    settings = load_settings()
    pair = settings["trading"]["symbol"]
    timeframe = settings["trading"]["timeframe"]
    limit = 15

    df = fetch_ohlcv(pair=pair, timeframe=timeframe, limit=limit)
    
    assert isinstance(df, pd.DataFrame), f"Expected DataFrame, got {type(df)}"
    assert not df.empty, "DataFrame should not be empty"
    assert len(df) == limit, f"Expected {limit} candles, got {len(df)}"

    expected_columns = ["timestamp", "open", "high", "low", "close", "volume"]
    assert list(df.columns) == expected_columns, f"Columns mismatch: {list(df.columns)}"

    # Confirm timestamps are in ascending order
    assert df["timestamp"].is_monotonic_increasing, "Timestamps must be strictly ascending"
    assert (df["timestamp"].diff().dropna() > 0).all(), "Candle timestamps must be strictly increasing"

    # Confirm all numeric price/volume columns are positive floats
    for col in ["open", "high", "low", "close", "volume"]:
        assert (df[col] >= 0).all(), f"Values in column {col} must be non-negative"


def test_fetch_order_book_live():
    """
    NOTE: Requires active internet access to Binance public REST API.
    Confirms fetch_order_book returns a dictionary with non-empty bids and asks lists.
    """
    settings = load_settings()
    pair = settings["trading"]["symbol"]
    depth = 10

    book = fetch_order_book(pair=pair, depth=depth)
    assert isinstance(book, dict), f"Expected dict, got {type(book)}"
    assert "bids" in book and "asks" in book, "Order book must contain 'bids' and 'asks'"

    bids = book["bids"]
    asks = book["asks"]

    assert isinstance(bids, list) and len(bids) > 0, "Bids list must be non-empty"
    assert isinstance(asks, list) and len(asks) > 0, "Asks list must be non-empty"
    assert len(bids) <= depth, f"Bids depth ({len(bids)}) exceeds requested ({depth})"
    assert len(asks) <= depth, f"Asks depth ({len(asks)}) exceeds requested ({depth})"

    # Verify best bid is less than best ask (valid spread)
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    assert best_bid < best_ask, f"Best bid ({best_bid}) should be less than best ask ({best_ask})"


def test_fetch_invalid_pair_raises_error():
    """
    NOTE: Requires active internet access.
    Confirms that querying an invalid/non-existent pair raises BinanceClientError.
    """
    with pytest.raises(BinanceClientError):
        fetch_current_price(pair="NONEXISTENT/USDT")

