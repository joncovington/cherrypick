"""The stores' contract: each table's columns and the spellings its coded columns hold (#1084).

Items 47 and 54 passed their tests and ran on zero trades: their invented fixtures spelt rights C/P
and the end-of-day store spells them CALL/PUT. The open-interest store spells them C/P. So the
contract (`store_contract.json`, committed beside this module) records, per store and table, the
columns and the distinct values of each coded column (`CODED`), spellings only, never a price. The
DDL is the suite's own; the spellings are formats the vendor's data arrives in, not its data.

- `write` (the `store_contract.py` script, local) reads the stores on disk and writes the contract;
- `check` compares the stores on disk with it (local), so a store that starts spelling a right
  another way is noticed;
- in CI, with no store, tests hold each store's DDL in code to the contract's columns and hold the
  study kit's readers to every spelling the contract records; fixtures take their spellings from
  `rights` instead of guessing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import chainfetch, intradayfetch, oifetch

CONTRACT_PATH = Path(__file__).with_name("store_contract.json")
#: The columns whose values are codes, read into the contract (at most `MAX_CODES` each).
CODED = ("right", "status")
MAX_CODES = 12
#: Each store the contract covers, with the DDL that creates it in code.
DDL = {
    "eod_chains.db": chainfetch.SCHEMA,
    "intraday_chains.db": intradayfetch.SCHEMA,
    "open_interest.db": ";\n".join(oifetch.DDL),
}


def columns_of(conn: sqlite3.Connection) -> dict[str, list[list[str]]]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: [[c[1], c[2]] for c in conn.execute(f"PRAGMA table_info({t})")] for t in tables}


def ddl_columns(ddl: str) -> dict[str, list[list[str]]]:
    """The tables and columns a DDL string creates, read back from an in-memory database."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl)
        return columns_of(conn)
    finally:
        conn.close()


def read_store(path: Path) -> dict:
    """One store on disk: its tables' columns and each coded column's distinct values."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = columns_of(conn)
        out = {}
        for table, cs in cols.items():
            codes = {}
            for name, _ in cs:
                if name in CODED:
                    vals = [
                        r[0]
                        for r in conn.execute(f"SELECT DISTINCT {name} FROM {table} LIMIT {MAX_CODES + 1}")
                    ]
                    codes[name] = sorted(v for v in vals if v is not None)
            out[table] = {"columns": cs, "codes": codes}
        return out
    finally:
        conn.close()


def build(vendor_dir: Path) -> dict:
    """The contract from the stores in `vendor_dir`: every store in `DDL` that exists there."""
    return {name: read_store(vendor_dir / name) for name in DDL if (vendor_dir / name).exists()}


def load() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def differences(contract: dict, actual: dict) -> list[str]:
    """What the stores on disk hold that the contract does not say (or the other way)."""
    out = []
    for store in sorted(set(contract) | set(actual)):
        c, a = contract.get(store), actual.get(store)
        if c is None or a is None:
            out.append(f"{store}: {'not in the contract' if c is None else 'not on disk'}")
            continue
        for table in sorted(set(c) | set(a)):
            ct, at = c.get(table), a.get(table)
            if ct is None or at is None:
                out.append(f"{store}.{table}: {'not in the contract' if ct is None else 'not on disk'}")
                continue
            if ct["columns"] != at["columns"]:
                out.append(f"{store}.{table}: columns differ")
            for col in sorted(set(ct["codes"]) | set(at["codes"])):
                if ct["codes"].get(col) != at["codes"].get(col):
                    out.append(f"{store}.{table}.{col}: codes {at['codes'].get(col)} against the contract's "
                               f"{ct['codes'].get(col)}")  # fmt: skip
    return out


def rights(store: str, table: str | None = None) -> tuple[str, str]:
    """The (call, put) spellings `store` uses, from the contract: fixtures build rows with these."""
    tables = load()[store]
    for name, t in tables.items():
        if (table is None or name == table) and "right" in t["codes"]:
            codes = t["codes"]["right"]
            call = next(v for v in codes if v.upper().startswith("C"))
            put = next(v for v in codes if v.upper().startswith("P"))
            return call, put
    raise KeyError(f"{store} records no right spelling")
