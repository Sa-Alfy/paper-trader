# Paper Trader 📈🤖

**Paper Trader** is an AI-assisted cryptocurrency paper-trading platform designed to test automated trading decisions against real-time Binance market data using a virtual (fake-money) ledger.

> ⚠️ **IMPORTANT DISCLAIMER**
> - **NO REAL MONEY**: This system uses a strictly isolated local SQLite database as a virtual ledger.
> - **NO REAL ORDERS**: No real orders are ever placed on any exchange.
> - **NO API KEYS REQUIRED**: All market data is retrieved strictly from public API endpoints via `ccxt`. No exchange authentication keys are required or supported.

---

## 🏗️ Project Architecture & Status

The project is structured modularly following this sequential build roadmap:

1. **`data/` (Market Data Connector)** ✅ *Implemented*
   Pulls live price tickers, candle OHLCV data, and order books from Binance public API using `ccxt`. Supports automatic fallback to `config/settings.yaml`.
2. **`db/` (Database Layer & Virtual Ledger)** ✅ *Implemented*
   SQLite database manager with foreign key enforcement (`PRAGMA foreign_keys = ON;`) for persistent storage of virtual balances, decisions, open/closed positions, executed paper trades, and performance evaluations. Implements an append-only ledger tracking cash flow invariants (`total_usd == available_usd + locked_usd`).
3. **`engine/` (Rule-Based Decision Engine)** ✅ *Implemented*
   - `indicators.py`: Calculates technical indicators via `pandas-ta` (EMA 20, EMA 50, RSI 14, MACD 12/26/9 line, signal, and histogram). Safely handles short historical windows (< 50 candles) without crashing.
   - `rules.py`: Deterministic multi-indicator consensus engine:
     - **Trend**: EMA(20) vs EMA(50) crossover & regime direction.
     - **Confirmation**: RSI overbought (>70) and oversold (<30) detection.
     - **Momentum**: MACD histogram positive/negative momentum.
     - **Consensus Rule**: Requires agreement from at least 2 of the 3 indicators to trigger `BUY` or `SELL`; otherwise defaults to `HOLD`.
     - Returns actionable signals, confidence scores (0.0–1.0), explainable plain-language reasoning referencing exact computed values, and an indicator snapshot.
4. **`simulator/` (Trade Simulator & Virtual Execution)** ⏳ *Next Step*
   Executes signals virtually against real market prices, applies simulated fees and slippage, updates ledger balances, and enforces hard risk caps (max position size, max daily trades).
5. **`evaluator/` (Performance Evaluator & Behavior Auditor)** ⏳ *Upcoming*
   Computes key trading metrics (Win Rate, Total PnL, Max Drawdown, Sharpe Ratio) and detects behavioral patterns/mistakes (e.g., overtrading, chasing pumps).
6. **`dashboard/` (Monitoring Interface)** ⏳ *Upcoming*
   A lightweight dashboard for visualizing live price streams, active virtual positions, trade history, and evaluator audit reports.
7. **`engine/llm.py` (AI Reasoning Layer)** ⏳ *Upcoming*
   Synthesizes market data and technical signals into AI-assisted trade reasoning and risk assessments before final simulation execution.

---

## 📁 Repository Layout

```text
paper trader/
├── config/
│   ├── config.py             # Settings loader and configuration access
│   └── settings.yaml          # Pair, timeframe, risk limits, and simulation config
├── data/
│   └── binance_client.py     # Public Binance API client (tickers, OHLCV, order books)
├── db/
│   ├── database.py           # Database connection & initialization utilities
│   └── schema.sql            # SQLite schema (balance, decisions, positions, trades, evaluations)
├── engine/
│   ├── indicators.py         # Technical indicator computation (pandas-ta)
│   └── rules.py              # Rule-based decision engine (EMA, RSI, MACD consensus)
├── simulator/
│   └── ledger.py             # Append-only virtual balance ledger
├── tests/
│   ├── test_data.py          # Market data API integration tests (marked @pytest.mark.network)
│   ├── test_db.py            # Database schema & ledger unit tests (offline)
│   └── test_engine.py        # Technical indicators and rule engine unit tests (offline)
├── pytest.ini                # Pytest configuration and markers
└── requirements.txt          # Python dependencies
```

---

## 🚀 Environment Setup & Quickstart

### 1. Python Installation (Windows)
Requires standalone **Python 3.12+**.
Install via `winget` (PowerShell):
```powershell
winget install --id Python.Python.3.12 --exact
```
Or download directly from [python.org](https://www.python.org/downloads/).

### 2. Virtual Environment & Dependencies
Create and activate an isolated project virtual environment (`venv`), then install required packages:

```powershell
# Create virtual environment
python -m venv venv

# Activate virtual environment (PowerShell)
.\venv\Scripts\Activate.ps1

# Install project dependencies into venv
pip install -r requirements.txt
```

### 3. Verification & Testing

Run the offline unit tests (no network access required):
```powershell
venv\Scripts\python.exe -m pytest -v -m "not network"
```

Run rule engine specific tests:
```powershell
venv\Scripts\python.exe -m pytest tests/test_engine.py -v
```

Run live network tests against public Binance endpoints:
```powershell
venv\Scripts\python.exe -m pytest tests/test_data.py -v
```

---

## ⚙️ Configuration
Edit `config/settings.yaml` to adjust trading pairs (`BTC/USDT`), virtual starting balance, position size limits, simulated fee percentages, and slippage assumptions.

---

## 🗄️ Database Initialization
The SQLite schema is defined in `db/schema.sql`. Foreign keys are strictly enforced on every database connection via `PRAGMA foreign_keys = ON;`.
