"""The arm, disarm, status and once commands, and the fence that keeps orders out of this path."""

import ast
import json
import pathlib

import pytest
from cherrypick.core import home as _home
from cherrypick.core import live as _live

from cherrypick.ladder import live_loop

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "cherrypick" / "ladder"
EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "config.example.json"
ACCOUNT = "12345670000"


def live_config(**live) -> dict:
    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    cfg["live"].update({"enabled": True, "gate0_confirmed": "owner 2026-11-20"})
    cfg["live"].update(live)
    return cfg


@pytest.fixture
def write_config(tmp_path):
    def write(cfg) -> str:
        path = tmp_path / "ladder.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def suite(managed_home):
    """The suite config under the managed home, with the module switched on or off."""

    def set_module(enabled: bool) -> None:
        managed_home.mkdir(parents=True, exist_ok=True)
        (managed_home / "config.json").write_text(
            json.dumps({"modules": {"ladder": {"enabled": enabled}}}), encoding="utf-8"
        )

    set_module(True)
    return set_module


@pytest.fixture(autouse=True)
def seams(monkeypatch):
    """A supervisor heartbeat, a designated account, and no real keyring."""
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda max_age_seconds=90: True)
    monkeypatch.setattr(live_loop, "_designated", lambda: ACCOUNT)


def armed_file() -> pathlib.Path:
    return _live.arm_record_path("ladder")


def run(capsys, *argv) -> tuple[int, str]:
    code = live_loop.main(list(argv))
    return code, capsys.readouterr().out


def parse(out: str) -> dict:
    """The JSON object a command prints last (the disclaimer, when shown, comes before it)."""
    return json.loads(out[out.rindex("\n{") + 1 :] if "\n{" in out else out)


# --------------------------------------------------------------------------- arm
def test_arm_with_exact_yes_prints_the_disclaimer_and_writes_the_record(capsys, suite, write_config):
    code, out = run(capsys, "--config", write_config(live_config()), "--arm", "--confirm", "YES")
    assert code == 0
    assert _live.disclaimer_text().strip().splitlines()[0] in out
    assert armed_file().exists()
    result = parse(out)
    assert result["ok"] and result["order_path"] is False


@pytest.mark.parametrize("confirm", [None, "yes", "Yes", "YES ", "Y", "no", ""])
def test_arm_refuses_without_the_exact_yes(capsys, suite, write_config, confirm):
    argv = ["--config", write_config(live_config()), "--arm"]
    if confirm is not None:
        argv += ["--confirm", confirm]
    code, out = run(capsys, *argv)
    assert code == 1 and not armed_file().exists()
    assert "Nothing was armed" in out


@pytest.mark.parametrize("gate", ["enabled", "gate0_confirmed"])
def test_arm_refuses_when_a_config_gate_is_off(capsys, suite, write_config, gate):
    cfg = live_config()
    cfg["live"][gate] = False if gate == "enabled" else ""
    code, out = run(capsys, "--config", write_config(cfg), "--arm", "--confirm", "YES")
    assert code == 1 and not armed_file().exists()
    assert f"live.{gate}" in out


def test_arm_refuses_while_the_module_is_off(capsys, suite, write_config):
    suite(False)
    code, out = run(capsys, "--config", write_config(live_config()), "--arm", "--confirm", "YES")
    assert code == 1 and not armed_file().exists()
    assert "switched off" in out


def test_arm_refuses_with_no_suite_config_at_all(capsys, write_config):
    code, _ = run(capsys, "--config", write_config(live_config()), "--arm", "--confirm", "YES")
    assert code == 1 and not armed_file().exists()


def test_arm_refuses_without_a_supervisor(capsys, suite, write_config, monkeypatch):
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda max_age_seconds=90: False)
    code, out = run(capsys, "--config", write_config(live_config()), "--arm", "--confirm", "YES")
    assert code == 1 and not armed_file().exists() and "no supervisor" in out


def test_install_task_arms_without_a_prompt_but_with_the_same_refusals(capsys, suite, write_config):
    path = write_config(live_config())
    code, out = run(capsys, "--config", path, "--install-task")
    assert code == 0 and armed_file().exists()
    assert "DISCLAIMER" not in out
    run(capsys, "--disarm")
    suite(False)
    code, _ = run(capsys, "--config", path, "--install-task")
    assert code == 1 and not armed_file().exists()
    suite(True)
    code, _ = run(capsys, "--config", write_config(live_config(enabled=False)), "--install-task")
    assert code == 1 and not armed_file().exists()


def test_the_record_arms_today_only(capsys, suite, write_config):
    run(capsys, "--config", write_config(live_config()), "--install-task")
    record = json.loads(armed_file().read_text(encoding="utf-8"))
    assert record["date"] == live_loop.now_et().date().isoformat()


def test_disarm_removes_the_record(capsys, suite, write_config):
    run(capsys, "--config", write_config(live_config()), "--install-task")
    code, _ = run(capsys, "--disarm")
    assert code == 0 and not armed_file().exists()
    code, _ = run(capsys, "--disarm")
    assert code == 1  # nothing was armed


# --------------------------------------------------------------------------- status and once
def test_status_masks_the_account_and_makes_no_broker_call(capsys, suite, write_config):
    code, out = run(capsys, "--config", write_config(live_config()), "--status")
    assert code == 0
    assert "****0000" in out and "1234567" not in out
    status = json.loads(out)
    assert status["order_path"] is False and status["account"] == "****0000"
    assert status["armed_today"] is False
    names = [g["name"] for g in status["gates"]]
    assert names == [
        "live.enabled",
        "live.gate0_confirmed",
        "modules.ladder.enabled",
        "arm.record",
        "account.designated",
        "halt.flag",
        "live.arm",
        "live.symbols",
        "live.limits",
    ]
    assert status["unmet_gates"] == ["arm.record"]


def test_status_shows_armed_today_after_an_arm(capsys, suite, write_config):
    path = write_config(live_config())
    run(capsys, "--config", path, "--install-task")
    _, out = run(capsys, "--config", path, "--status")
    assert json.loads(out)["armed_today"] is True


def test_status_reports_the_halt_flag_as_blocking_new_buys_only(capsys, suite, write_config, managed_home):
    path = write_config(live_config())
    run(capsys, "--config", path, "--install-task")
    flag = _home.halt_flag_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("", encoding="utf-8")
    _, out = run(capsys, "--config", path, "--status")
    status = json.loads(out)
    assert status["halt_flag"] is True and status["new_buys_allowed"] is False
    assert [g for g in status["gates"] if g["name"] == "halt.flag"][0]["blocks"] == "new_buys"


def test_status_with_the_example_as_shipped_is_not_ready(capsys, suite):
    code, out = run(capsys, "--status")
    status = json.loads(out)
    assert code == 0 and status["ready"] is False
    assert {"live.enabled", "live.gate0_confirmed"} <= set(status["unmet_gates"])


def test_status_without_an_account_names_the_gate(capsys, suite, write_config, monkeypatch):
    monkeypatch.setattr(live_loop, "_designated", lambda: None)
    _, out = run(capsys, "--config", write_config(live_config()), "--status")
    status = json.loads(out)
    assert status["account"] is None and "account.designated" in status["unmet_gates"]


def test_once_only_evaluates_and_says_there_is_no_order_path(capsys, suite, write_config):
    code, out = run(capsys, "--config", write_config(live_config()), "--once")
    assert code == 0
    status = json.loads(out)
    assert status["order_path"] is False and "no order path" in status["note"]
    log = live_loop.log_file().read_text(encoding="utf-8")
    assert "no order path yet" in log


# --------------------------------------------------------------------------- the fence
FORBIDDEN = ("broker", "execution", "live_orders", "tastytrade", "session", "order")
FENCED = ("live_loop.py", "live_gates.py", "credentials.py")


def imported_names(source: str) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names += [base] + [f"{base}.{a.name}" for a in node.names]
    return names


def violations(source: str) -> list[str]:
    return [n for n in imported_names(source) if any(word in n.lower() for word in FORBIDDEN)]


@pytest.mark.parametrize("name", FENCED)
def test_the_live_path_imports_no_order_or_broker_trading_code(name):
    assert violations((SRC / name).read_text(encoding="utf-8")) == []


def test_there_is_no_order_module_beside_the_live_path():
    present = {p.name for p in SRC.glob("*.py")}
    assert not present & {"live_orders.py", "broker_cli.py", "orders.py"}


def test_the_fence_catches_an_order_import():
    """The scan has to be able to fail: each of these must be flagged."""
    for line in (
        "from cherrypick.core import execution",
        "from cherrypick.core.broker import cli",
        "from cherrypick.ladder import live_orders",
        "import tastytrade",
        "from cherrypick.core.auth import get_session",
    ):
        assert violations(line + "\n"), line


def test_once_names_no_order_call():
    """`--once` is `run_once`; it reads the gates and logs. Neither function mentions an order call."""
    tree = ast.parse((SRC / "live_loop.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for fn in ("run_once", "status", "gates_now"):
        called = {
            (n.id if isinstance(n, ast.Name) else n.attr)
            for n in ast.walk(funcs[fn])
            if isinstance(n, ast.Name | ast.Attribute)
        }
        for word in ("place", "submit", "cancel", "replace", "get_session", "broker"):
            assert not [c for c in called if word in c.lower()], (fn, word)
