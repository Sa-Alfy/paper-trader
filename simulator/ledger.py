import sqlite3
from typing import Dict


class Ledger:
    """
    Virtual balance ledger manager.
    
    Design Choice: APPEND-ONLY HISTORY
    - The `balance` table acts as an append-only transaction ledger.
    - Every balance update (deposit, locking capital for a trade, releasing capital, settling PnL)
      inserts a NEW snapshot row with timestamp.
    - The 'current' active balance is retrieved by querying the latest record (`ORDER BY id DESC LIMIT 1`).
    """

    @staticmethod
    def initialize_ledger(conn: sqlite3.Connection, starting_balance_usd: float) -> Dict[str, float]:
        """
        Initializes the balance ledger with a starting balance atomically if empty.
        """
        cursor = conn.cursor()
        # Atomic check-and-insert using conditional SELECT ... WHERE NOT EXISTS
        cursor.execute(
            """
            INSERT INTO balance (total_usd, available_usd, locked_usd)
            SELECT ?, ?, 0.0
            WHERE NOT EXISTS (SELECT 1 FROM balance)
            """,
            (starting_balance_usd, starting_balance_usd)
        )
        conn.commit()
        return Ledger.get_current_balance(conn)

    @staticmethod
    def get_current_balance(conn: sqlite3.Connection) -> Dict[str, float]:
        """
        Retrieves the latest current balance snapshot (Append-Only ledger reader).
        Enforces invariant: total_usd == available_usd + locked_usd.
        """
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT total_usd, available_usd, locked_usd, timestamp
            FROM balance
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("Balance ledger is uninitialized. Call initialize_ledger first.")

        total = row["total_usd"]
        available = row["available_usd"]
        locked = row["locked_usd"]

        # Invariant check: total must equal available + locked within float precision tolerance
        if abs(total - (available + locked)) > 1e-5:
            raise ValueError(
                f"Balance invariant violated! total ({total}) != available ({available}) + locked ({locked})"
            )

        return {
            "total_usd": total,
            "available_usd": available,
            "locked_usd": locked,
            "timestamp": row["timestamp"]
        }

    @staticmethod
    def record_balance_snapshot(
        conn: sqlite3.Connection,
        total_usd: float,
        available_usd: float,
        locked_usd: float
    ) -> Dict[str, float]:
        """
        Appends a new balance snapshot record to the ledger history.
        MUST be called within an active database transaction.
        Enforces invariant: total_usd == available_usd + locked_usd.
        """
        # Invariant check before inserting snapshot
        if abs(total_usd - (available_usd + locked_usd)) > 1e-5:
            raise ValueError(
                f"Cannot record invalid balance! total ({total_usd}) != available ({available_usd}) + locked ({locked_usd})"
            )

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO balance (total_usd, available_usd, locked_usd)
            VALUES (?, ?, ?)
            """,
            (total_usd, available_usd, locked_usd)
        )
        return {
            "total_usd": total_usd,
            "available_usd": available_usd,
            "locked_usd": locked_usd
        }
