-- SQLite Database Schema for Paper Trader
PRAGMA foreign_keys = ON;

-- Table 1: Balance (Virtual Ledger Cash Flow)
CREATE TABLE IF NOT EXISTS balance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    total_usd REAL NOT NULL,
    available_usd REAL NOT NULL,
    locked_usd REAL NOT NULL DEFAULT 0.0
);

-- Table 2: Decisions (Engine & LLM outputs, whether acted on or rejected)
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    pair TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
    confidence REAL,
    indicators_json TEXT, -- Serialized technical indicators at decision time
    reasoning TEXT NOT NULL, -- Textual explanation / rule breakdown / LLM prompt response
    acted_on BOOLEAN NOT NULL DEFAULT 0, -- 1 if simulator executed, 0 if skipped/rejected by risk caps
    rejection_reason TEXT -- Reason if not acted on (e.g., 'max position cap reached')
);

-- Table 3: Positions (Open and Closed Trade Containers)
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
    entry_price REAL NOT NULL,
    size REAL NOT NULL, -- Base asset size (e.g. BTC amount)
    cost_basis_usd REAL NOT NULL,
    current_price REAL,
    realized_pnl REAL DEFAULT 0.0,
    unrealized_pnl REAL DEFAULT 0.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME
);

-- Table 4: Trades (Executed Paper Transactions)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL REFERENCES positions(id),
    decision_id INTEGER NOT NULL REFERENCES decisions(id),
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    price REAL NOT NULL,
    size REAL NOT NULL,
    fee REAL NOT NULL, -- Simulated fee in USD
    fee_asset TEXT NOT NULL DEFAULT 'USDT',
    slippage REAL NOT NULL DEFAULT 0.0, -- Simulated slippage applied
    executed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Table 5: Evaluations (Periodic Performance & Behavior Pattern Audits)
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_timestamp DATETIME NOT NULL,
    end_timestamp DATETIME NOT NULL,
    total_trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    total_pnl REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    sharpe_ratio REAL,
    mistakes_json TEXT, -- Structured tags e.g. ["overtrading", "chasing_pump"]
    behavior_tags_json TEXT, -- Behavioral observations
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance & quick lookups
CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades(executed_at);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
