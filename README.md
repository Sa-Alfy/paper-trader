# Paper Trader 📈🤖

**Paper Trader** is an AI-assisted cryptocurrency paper-trading platform designed to test automated trading decisions against real-time Binance market data using a virtual (fake-money) ledger.

> ⚠️ **IMPORTANT DISCLAIMER**
> - **NO REAL MONEY**: This system uses a strictly isolated local SQLite database as a virtual ledger.
> - **NO REAL ORDERS**: No real orders are ever placed on any exchange.
> - **NO API KEYS REQUIRED**: All market data is retrieved strictly from public API endpoints via `ccxt`. No exchange authentication keys are required or supported.

---

## 🏗️ Project Architecture & Build Order

The project is structured modularly following this sequential build roadmap:

1. **`data/` (Market Data Connector)**
   Pulls live price tickers, candle OHLCV data, and order books from Binance public API using `ccxt`.
2. **`db/` (Database Layer)**
   SQLite database manager for persistent storage of virtual balances, decisions, open/closed positions, executed paper trades, and performance evaluations.
3. **`engine/` (Decision Engine)**
   Calculates technical indicators (RSI, MACD, SMA, EMA, ATR) and generates deterministic rule-based buy/sell/hold trading signals.
4. **`simulator/` (Trade Simulator & Virtual Ledger)**
   Executes signals virtually against real market prices, applies simulated fees and slippage, updates ledger balances, and enforces hard risk caps (max position size, max daily trades).
5. **`evaluator/` (Performance Evaluator & Behavior Auditor)**
   Computes key trading metrics (Win Rate, Total PnL, Max Drawdown, Sharpe Ratio) and detects behavioral patterns/mistakes (e.g., overtrading, chasing pumps).
6. **`dashboard/` (Monitoring Interface)**
   A lightweight dashboard for visualizing live price streams, active virtual positions, trade history, and evaluator audit reports.
7. **`engine/llm.py` (AI Reasoning Layer - Added Last)**
   Synthesizes market data and technical signals into AI-assisted trade reasoning and risk assessments before final simulation execution.

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
Run unit tests inside the activated environment:
```powershell
pytest tests/test_db.py -v
```

---

## ⚙️ Configuration
Edit `config/settings.yaml` to adjust trading pairs (`BTC/USDT`), virtual starting balance, position size limits, simulated fee percentages, and slippage assumptions.

---

## 🗄️ Database Initialization
The SQLite schema is defined in `db/schema.sql`. Foreign keys are strictly enforced on every database connection via `PRAGMA foreign_keys = ON;`.
