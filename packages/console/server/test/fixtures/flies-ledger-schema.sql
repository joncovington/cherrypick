-- Flies ledger schema used by fixtures
CREATE TABLE fly_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  trade_date TEXT NOT NULL,
  arm TEXT,
  symbol TEXT,
  mode TEXT,
  outcome TEXT,
  block_detail TEXT,
  reason TEXT,
  accepted INTEGER,
  first_seen TEXT,
  last_seen TEXT,
  occurrences INTEGER,
  center REAL,
  wing_width REAL,
  far_width REAL,
  center_first REAL,
  center_last REAL,
  blocking_strike REAL,
  seconds_until_cadence_clear REAL,
  underlying_price REAL,
  position_id TEXT,
  detail TEXT
);
CREATE INDEX idx_fly_decisions_date ON fly_decisions (trade_date);
CREATE TABLE fly_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  arm TEXT,
  symbol TEXT,
  kind TEXT,
  side INTEGER,
  center REAL,
  wing_width REAL,
  far_width REAL,
  status TEXT,
  void_reason TEXT
);
CREATE INDEX idx_fly_positions_date ON fly_positions (trade_date);
CREATE TABLE fly_entry_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  trade_date TEXT NOT NULL,
  arm TEXT,
  symbol TEXT,
  outcome TEXT,
  block_detail TEXT,
  center REAL,
  wing_width REAL,
  far_width REAL,
  blocking_strike REAL,
  seconds_until_cadence_clear REAL,
  underlying_price REAL,
  spot REAL
);
CREATE INDEX idx_fly_entry_attempts_date ON fly_entry_attempts (trade_date);
