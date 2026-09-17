import pytest
import sqlite3
from pathlib import Path
from db.database import get_connection, init_db
from simulator.ledger import Ledger


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_paper_trader.db"
    schema_file = Path(__file__).parent.parent / "db" / "schema.sql"
    init_db(db_path=db_file, schema_path=schema_file)
    return db_file


def test_pragma_foreign_keys_enabled(temp_db):
    """Confirm PRAGMA foreign_keys = ON is enforced for every connection."""
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys;")
    fk_status = cursor.fetchone()[0]
    conn.close()
    assert fk_status == 1, "PRAGMA foreign_keys must be 1 (ON)"


def test_foreign_key_constraint_enforcement(temp_db):
    """Confirm invalid foreign keys are rejected by SQLite."""
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    
    with pytest.raises(sqlite3.IntegrityError):
        # Attempting to insert trade with non-existent position_id and decision_id
        cursor.execute(
            """
            INSERT INTO trades (position_id, decision_id, pair, side, price, size, fee)
            VALUES (999, 999, 'BTC/USDT', 'BUY', 50000.0, 0.1, 5.0)
            """
        )
    conn.close()


def test_append_only_ledger_history(temp_db):
    """Confirm ledger operates as an append-only time series with invariant checks."""
    conn = get_connection(temp_db)
    
    # 1. Initialize ledger
    bal1 = Ledger.initialize_ledger(conn, starting_balance_usd=10000.0)
    assert bal1["total_usd"] == 10000.0
    assert bal1["available_usd"] == 10000.0
    assert bal1["locked_usd"] == 0.0

    # 2. Lock capital for a trade
    Ledger.record_balance_snapshot(conn, total_usd=10000.0, available_usd=9000.0, locked_usd=1000.0)
    conn.commit()

    bal2 = Ledger.get_current_balance(conn)
    assert bal2["available_usd"] == 9000.0
    assert bal2["locked_usd"] == 1000.0

    # 3. Verify total row count is 2 (Append-Only)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM balance")
    count = cursor.fetchone()["count"]
    assert count == 2

    conn.close()


def test_balance_invariant_enforcement(temp_db):
    """Confirm ledger raises ValueError for balance snapshots where total != available + locked."""
    conn = get_connection(temp_db)
    Ledger.initialize_ledger(conn, starting_balance_usd=10000.0)

    # Attempt to record inconsistent balance snapshot
    with pytest.raises(ValueError, match="Cannot record invalid balance"):
        Ledger.record_balance_snapshot(conn, total_usd=10000.0, available_usd=5000.0, locked_usd=1000.0)

    conn.close()
