"""scripts/demo_home.py: the fake home the documentation screenshots are taken on.

It is a developer tool, loaded here by running it as a subprocess (it sets CHERRYPICK_HOME for itself
and imports the packages' writers, neither of which belongs in the test process). Two things are
pinned: the same seed writes the same files, and nothing in the output names a trade ticker or an
account the demo rules do not allow.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "demo_home.py"
ARGS = ["--days", "12"]

ALLOWED_TRADE_NAMES = {"SPX", "XSP", "SPY", "QQQ", "IWM", "AAA", "BBB", "CCC", "DDD"}


def generate(out: Path, *extra: str) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), *ARGS, *extra],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr[-2000:]


def digest(root: Path) -> dict[str, str]:
    """A hash of every file; a SQLite file by its SQL dump, so page layout cannot matter."""
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".db":
            con = sqlite3.connect(path)
            body = "\n".join(con.iterdump()).encode()
            con.close()
        else:
            body = (
                path.read_bytes()
                .replace(str(root).encode(), b"<HOME>")
                .replace(root.as_posix().encode(), b"<HOME>")
            )
        out[rel] = hashlib.sha256(body).hexdigest()
    return out


@pytest.fixture(scope="module")
def homes(tmp_path_factory) -> tuple[Path, Path, Path]:
    base = tmp_path_factory.mktemp("demo")
    a, b, c = base / "a", base / "b", base / "c"
    generate(a)
    generate(b)
    generate(c, "--seed", "8")
    return a, b, c


def test_the_same_seed_writes_the_same_files(homes):
    a, b, _ = homes
    da, db = digest(a), digest(b)
    assert set(da) == set(db)
    assert [k for k in da if da[k] != db[k]] == []


def test_another_seed_writes_other_numbers(homes):
    a, _, c = homes
    da, dc = digest(a), digest(c)
    assert da["data/meic/paper_trades.db"] != dc["data/meic/paper_trades.db"]


def test_the_shipped_modules_are_on_and_the_retired_ones_off(homes):
    cfg = json.loads((homes[0] / "config.json").read_text(encoding="utf-8"))
    on = {k for k, v in cfg["modules"].items() if v["enabled"]}
    assert on == {"meic", "earnings", "calendars", "contango", "weeklyput"}
    assert (
        json.loads((homes[0] / "config" / "console.json").read_text(encoding="utf-8"))["serve"]["port"]
        != 5070
    )


def test_no_forbidden_account_or_trade_ticker(homes):
    """Account numbers and every traded symbol in a ledger or the evaluation are the demo's own."""
    home = homes[0]
    text = (home / "data" / "manage" / "eval-2026-11-06.json").read_text(encoding="utf-8")
    assert not re.search(r'"account": "(?!\*\*\*\*0000")', text)
    ev = json.loads((home / "data" / "manage" / "eval-2026-11-06.json").read_text(encoding="utf-8"))
    assert [a["account"] for a in ev["accounts"]] == ["****0000"]
    assert {t["underlying"] for a in ev["accounts"] for t in a["trades"]} <= ALLOWED_TRADE_NAMES
    symbols: set[str] = set()
    for db_path, table, column in (
        ("meic", "ic_trades", "symbol"),
        ("earnings", "trades", "symbol"),
        ("calendars", "dc_positions", "symbol"),
        ("contango", "contango_positions", "symbol"),
        ("weeklyput", "weeklyput_positions", "symbol"),
    ):
        con = sqlite3.connect(home / "data" / db_path / "paper_trades.db")
        symbols |= {r[0] for r in con.execute(f"SELECT DISTINCT {column} FROM {table}")}
        con.close()
    assert symbols and symbols <= ALLOWED_TRADE_NAMES, symbols - ALLOWED_TRADE_NAMES


def test_the_screener_files_name_only_the_demo_symbols(homes):
    home = homes[0]
    income = json.loads(
        next((home / "data" / "technicals" / "income-lists").glob("*.json")).read_text(encoding="utf-8")
    )
    names = {r["symbol"] for rows in income["lists"].values() for r in rows}
    assert names and names <= ALLOWED_TRADE_NAMES


def test_the_ledgers_add_up(homes):
    """Each MEIC condor: gross is credit less the cost to close, and net is gross less fees (money-layout)."""
    con = sqlite3.connect(homes[0] / "data" / "meic" / "paper_trades.db")
    rows = con.execute("SELECT net_credit, exit_price, pnl, fees, quantity FROM ic_trades").fetchall()
    con.close()
    assert rows
    for credit, exit_price, pnl, fees, qty in rows:
        assert pnl == pytest.approx((credit - exit_price) * 100 * qty, abs=0.011)
        assert fees > 0


def test_generated_files_carry_no_username_or_real_repo_path(homes):
    import getpass

    assert (homes[0] / ".demo-home").is_file()
    loaded = load_script("demo_home")
    assert loaded.leaks_in(homes[0]) == []
    cfg = (homes[0] / "config.json").read_text(encoding="utf-8")
    assert "/opt/cherrypick/packages/meic" in cfg
    assert getpass.getuser().encode() not in b"".join(p.read_bytes() for p in homes[0].rglob("*.json"))


def test_the_leak_check_fails_when_a_path_leaks(tmp_path):
    loaded = load_script("demo_home")
    (tmp_path / "x.json").write_text(f'{{"path": "{loaded.REPO.as_posix()}/packages"}}', encoding="utf-8")
    assert loaded.leaks_in(tmp_path) == ["x.json"]


# ---------------------------------------------------------------------------- the launcher's guards
def load_script(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_home(base: Path, port: int, marker: bool = True) -> Path:
    (base / "config").mkdir(parents=True)
    (base / "config" / "console.json").write_text(json.dumps({"serve": {"port": port}}), encoding="utf-8")
    if marker:
        (base / ".demo-home").write_text("demo\n", encoding="utf-8")
    return base


def test_launcher_serves_a_demo_home_on_its_own_port(tmp_path):
    launcher = load_script("demo_console")
    real = make_home(tmp_path / "real", 5070, marker=False)
    assert launcher.check(make_home(tmp_path / "demo", 5391), real=real) == 5391


def test_launcher_refuses_a_home_without_the_marker(tmp_path):
    launcher = load_script("demo_console")
    real = make_home(tmp_path / "real", 5070, marker=False)
    with pytest.raises(SystemExit, match="marker"):
        launcher.check(make_home(tmp_path / "plain", 5391, marker=False), real=real)


def test_launcher_refuses_port_5070_and_the_real_consoles_port(tmp_path):
    launcher = load_script("demo_console")
    real = make_home(tmp_path / "real", 5123, marker=False)
    with pytest.raises(SystemExit, match="5070"):
        launcher.check(make_home(tmp_path / "a", 5070), real=real)
    with pytest.raises(SystemExit, match="5123"):
        launcher.check(make_home(tmp_path / "b", 5123), real=real)


def test_launcher_refuses_the_real_home_and_an_env_that_names_it(tmp_path):
    launcher = load_script("demo_console")
    real = make_home(tmp_path / "real", 5391)  # even carrying a marker and a free port
    with pytest.raises(SystemExit, match="real home"):
        launcher.check(real, real=real)
    with pytest.raises(SystemExit, match="CHERRYPICK_HOME"):
        launcher.check(make_home(tmp_path / "demo", 5391), real=real, env_home=str(real))


def test_launcher_switches_both_keyrings_off_and_drops_credential_variables(tmp_path):
    launcher = load_script("demo_console")
    env = launcher.demo_env(
        {"PATH": "p", "BROKER_TOKEN": "x", "MY_OAUTH": "y", "NODE_OPTIONS": "--foo"}, tmp_path
    )
    assert env["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"
    assert env["CHERRYPICK_HOME"] == str(tmp_path)
    assert "BROKER_TOKEN" not in env and "MY_OAUTH" not in env
    assert env["NODE_OPTIONS"].startswith("--foo") and "demo_console_nokeyring.mjs" in env["NODE_OPTIONS"]
    assert (ROOT / "scripts" / "demo_console_nokeyring_hooks.mjs").is_file()


def test_it_will_not_write_over_a_real_home(tmp_path):
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(tmp_path / ".cherrypick")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert done.returncode != 0 and "refusing" in done.stderr
