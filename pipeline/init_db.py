import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_sellout (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    channel  TEXT    NOT NULL,
    year     INTEGER NOT NULL,
    week     TEXT    NOT NULL,
    model    TEXT    NOT NULL,
    category TEXT,
    qty      REAL,
    sellthru REAL,
    UNIQUE(channel, year, week, model)
);

CREATE TABLE IF NOT EXISTS price_weekly (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    year              INTEGER NOT NULL,
    week              TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    avg_sale_price    REAL,
    avg_discount_rate REAL,
    UNIQUE(year, week, model)
);

CREATE TABLE IF NOT EXISTS season_vars (
    week         TEXT PRIMARY KEY,
    ramadan_flag INTEGER DEFAULT 0,
    summer_flag  INTEGER DEFAULT 0,
    holiday_flag INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    model        TEXT,
    date_from    TEXT,
    date_to      TEXT,
    requester    TEXT,
    status       TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS fcst_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    model        TEXT,
    week         TEXT,
    predicted    REAL,
    actual       REAL,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS fcst_accuracy_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    week       TEXT,
    level      TEXT,
    model      TEXT,
    mape       REAL,
    retrained  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fcst_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    week         TEXT NOT NULL,
    model        TEXT NOT NULL,
    level        TEXT,
    predicted    REAL,
    ci_low       REAL,
    ci_high      REAL,
    UNIQUE(week, model)
);
"""

def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

if __name__ == "__main__":
    import os
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    init_db(db_path)
    print(f"Database initialized: {db_path}")
