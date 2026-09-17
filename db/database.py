import sqlite3
from pathlib import Path
from typing import Union

DEFAULT_DB_PATH = Path(__file__).parent / "paper_trader.db"
DEFAULT_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Creates and returns a SQLite database connection with PRAGMA foreign_keys = ON
    enforced on every connection.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Enforce foreign key constraints explicitly for every new connection
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    schema_path: Union[str, Path] = DEFAULT_SCHEMA_PATH
) -> None:
    """
    Initializes the database schema using db/schema.sql if tables do not exist.
    Explicitly closes connection upon completion.
    """
    schema_file = Path(schema_path)
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_file.resolve()}")

    conn = get_connection(db_path)
    try:
        with conn:
            with open(schema_file, "r", encoding="utf-8") as f:
                schema_script = f.read()
            conn.executescript(schema_script)
    finally:
        conn.close()
