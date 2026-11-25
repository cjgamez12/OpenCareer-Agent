"""Small on/off settings saved in the database (e.g. new-grad mode)."""
import sqlite3

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);"


def get_flag(conn: sqlite3.Connection, key: str) -> bool | None:
    """True / False if you've set it, None if you never have."""
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0] == "1"


def set_flag(conn: sqlite3.Connection, key: str, value: bool) -> None:
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (key, "1" if value else "0"))
