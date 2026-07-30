"""Every subprocess the orchestrator spawns must be headless — enforced, not prose.

The scheduled tasks run under pythonw.exe, where the parent has no console. A console-subsystem child
(python.exe, powershell, schtasks, ...) launched without CREATE_NO_WINDOW then creates a brand-new
VISIBLE console — a terminal window flashing on the user's screen on every watchdog tick, daemon
restart, and desktop toast (observed 2026-07-29 restarting the streamer and services). Even
`-WindowStyle Hidden` flashes: the console is created before PowerShell hides it.

So the rule: every `subprocess.run` / `subprocess.Popen` call site in the orchestrator passes
`creationflags` (CREATE_NO_WINDOW from orchestrator.util, or an equivalent inline constant where a
module deliberately imports nothing — the notifier). The one exception is connect.py, whose delegated
credential entry is INTERACTIVE by design — the module's own tool prompts for secrets with hidden
input in the user's console, so it must share that console.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "cherrypick"

# connect.py delegates hidden-input secret entry to the module's own CLI in the user's console.
INTERACTIVE_EXEMPT = {"connect.py"}


def _subprocess_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr in ("run", "Popen")
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "subprocess"
        ):
            yield node


def test_every_subprocess_call_passes_creationflags():
    offenders = []
    for py in sorted(SRC.rglob("*.py")):
        if py.name in INTERACTIVE_EXEMPT:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for call in _subprocess_calls(tree):
            if not any(kw.arg == "creationflags" for kw in call.keywords):
                offenders.append(f"{py.relative_to(SRC)}:{call.lineno}")
    assert not offenders, (
        "subprocess call(s) without creationflags — a console window will flash when the parent is "
        f"windowless (pythonw). Pass creationflags=CREATE_NO_WINDOW: {offenders}"
    )


def test_the_interactive_exemption_is_still_real():
    """If connect.py stops spawning subprocesses, the exemption should be deleted, not linger."""
    tree = ast.parse((SRC / "orchestrator" / "connect.py").read_text(encoding="utf-8"))
    assert any(_subprocess_calls(tree)), "connect.py no longer spawns subprocesses — drop the exemption"
