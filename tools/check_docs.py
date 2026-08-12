#!/usr/bin/env python3
r"""Enforce the suite's documentation guardrails, instead of auditing for them by hand.

This suite already prefers executable invariants to prose -- orchestrator's `test_headless.py` is a
source scan, and `test_schema_registry.py` enforces schema coverage "not prose". This is the same
idea for the guardrails that live in every CLAUDE.md:

  1. No machine-specific absolute paths in tracked files (the "portable paths only" guardrail).
  2. No relative links that don't resolve (a doc pointing at a file that isn't there).
  3. No unmasked account numbers (the "mask to ****1234" guardrail).
  4. No out-of-repo pointers presented as authoritative (e.g. `~/.claude/plans/...`), which is how
     78KB of design ended up living on exactly one machine, untracked.
  5. No stale paths in ASCII file-layout trees. These are hand-maintained and drift silently: the
     namespace migration moved every module to src/cherrypick/<pkg>/ and both package READMEs still
     drew a flat src/, while two deleted slash commands stayed listed for a whole cleanup pass. A
     tree is a claim about the filesystem, so it can be checked like one.
  6. No unclosed markdown links -- `](path` with no `)`. Rule 2's regex needs the closing paren to
     match at all, so a truncated link is invisible to it: the link renders as literal text and the
     broken-link check never fires. Exactly one existed, and it had survived several doc passes.
  7. No documented command that points at nothing -- a `-m cherrypick.…` naming a module that does
     not exist, or a script path that resolves from nowhere a reader could plausibly be standing.
     This is the class that outlived every hand sweep, because the path was hidden inside something
     that did not look like a path: three commands ran `Start-Process python -ArgumentList
     'src\streamer.py'` for weeks after the migration moved that file, while the sweeps were
     matching `src/<mod>.py`. Prose can be stale and merely misleading; a command is either right
     or it is broken, so it is worth checking mechanically.
  8. No drift between `cli.py`'s subcommands and the CLI reference that claims to list them all.
     Both directions: an undocumented command is invisible to readers, a documented-but-removed one
     sends them at something that exits non-zero, and neither file is individually wrong -- only
     their disagreement is.
  9. No drift between a port declared in code and the runbook's port table. The table presents itself
     as a complete loopback inventory, which is what makes an omission dangerous: `/uninstall` reads
     it to decide what to shut down, so four missing rows left four servers running.
 10. No config key documented in `config.example.json` that nothing in the suite mentions. A dead key
     reads as a supported knob, and the one that prompted this carried a note stating a reason for its
     own retention that was false.
 11. No package missing from the four indexes that claim to list them all. The suite grew 7 -> 10 and
     every index stopped at 7 independently, so console -- 17,920 lines -- was documented nowhere.
 12. No drift between the AI insight's real tool policy and the docs that describe it. This is the
     only rule here guarding a *security* claim, which is why it exists: three files stated the agent
     "can't reach the network" while `WebSearch` was granted by default.

Run: python tools/check_docs.py
Exits non-zero on any finding, printing `path:line: message` so it reads like a linter.

Scope is `git ls-files` -- tracked files only. Untracked scratch and gitignored local tooling are
deliberately out of scope.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/)[A-Za-z0-9._-]+", re.I)
_HOME_POINTER = re.compile(r"~[\\/]\.claude[\\/]")
# 5+ consecutive digits adjacent to account wording, but not a masked ****1234 and not a year/port.
_ACCOUNT = re.compile(r"account[^\n]{0,24}?\b(?<!\*)(\d{5,})\b", re.I)
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
#: `](` that never closes on its line. Anchored on the same line deliberately -- a genuine multi-line
#: link is not idiomatic here, and allowing them would hide the truncation this is meant to catch.
_MD_LINK_OPEN = re.compile(r"\]\(([^)\n]*)$")
_FENCE = re.compile(r"^\s*```")
#: A tree row: leading indent (spaces plus the │ continuation bars), a branch marker, then the name.
_TREE_ROW = re.compile(r"^(?P<indent>[\s│|]*)(?:├──|└──|\|--|`--)\s*(?P<name>\S+)")
#: Placeholders (`eod-<date>.md`, `*.log`, `…`) name a shape, not a file. Nothing to resolve.
_PLACEHOLDER = ("<", ">", "*", "?", "...", "…")
#: A tree row may say outright that its entry is runtime-created and therefore absent from a fresh
#: checkout ("logs/  # Created at first run (gitignored; all rotated)"). Believe the annotation --
#: it is the author declaring the absence is intended, which is exactly what this check should not
#: flag. git check-ignore cannot settle it: a directory-only pattern like `logs/` will not match a
#: path that does not exist yet, because git cannot tell it is a directory.
_RUNTIME_MARKERS = ("gitignored", "created at", "created on", "at first run", "runtime")
#: A line inside a fence that actually invokes something, as opposed to sample output or a tree row.
_CMD_LINE = re.compile(r"\b(?:python3?|pythonw|Start-Process)\b")
#: A script path used as a command argument. The separator is required: a *bare* `run.py` or `tt.py`
#: is context-dependent ("run this from packages/orchestrator") and is also what tree rows are made
#: of, so demanding one keeps this rule off both. A separator is what the real bug class had --
#: `src/streamer.py` and the backslashed `'src\streamer.py'` inside a PowerShell argument list.
_CMD_PATH = re.compile(r"[\w.@-]*[/\\][\w./\\@-]*\.py\b")
#: `-m some.dotted.module`, including the quoted/comma form PowerShell needs ('-m','cherrypick.x').
_DASH_M = re.compile(r"-m[\s,'\"]+['\"]?(cherrypick\.[\w.]+)")

TEXT_SUFFIXES = {".md", ".toml", ".ini", ".cfg", ".yml", ".yaml", ".json", ".ps1", ".sh", ".py"}
DOC_SUFFIXES = {".md"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [ROOT / p for p in out if p.strip()]


def _is_quoted_example(line: str, pos: int) -> bool:
    """True when the match at `pos` sits inside a backtick span -- the doc is *quoting* the bad
    pattern rather than committing it.

    This replaced a line-level heuristic that exempted the entire line whenever it contained "no",
    "not ", "never", "avoid", ... anywhere in it. That fails open, and it did so on the two
    security-adjacent rules: a line reading "there is no reason this ran from C:/Users/me/secret"
    was silently exempt from both the absolute-path and unmasked-account checks, because it happens
    to contain the word "no".

    Requiring the match itself to be quoted is tighter and closer to the real intent -- every
    legitimate counter-example in this repo writes the offending path in backticks
    (``Never hardcode absolute paths (`C:\\Users\\...`)``), so nothing genuine depended on the
    looser rule. Scoped per match rather than per line, so one quoted example cannot excuse a real
    violation sitting further along the same line.
    """
    return line[:pos].count("`") % 2 == 1


def _is_test(rel: str) -> bool:
    """Tests legitimately hold synthetic fixtures that look like violations.

    Two real cases this exists for, both false positives when the checks ran repo-wide:
    `test_tasks_cron.py` uses `/home/me/backup.sh` as a stand-in for a *foreign* crontab line it must
    not clobber, and `test_util.py` passes a bare `12345678` straight into `mask_account()` -- that is
    the test OF the masker, the opposite of a leak.

    So the path/account checks skip tests. The guardrails they enforce are about code that actually
    runs and about what reaches a reader; a fixture string is neither. Broken-link checking still
    applies everywhere, since that has no equivalent ambiguity.
    """
    return "/tests/" in rel or rel.startswith("tests/")


#: This file is necessarily full of examples of every pattern it detects -- the regexes themselves,
#: the message strings, and the docstring explaining the two real false positives. Linters routinely
#: exempt their own rule definitions for exactly this reason. Contorting the strings to dodge a
#: self-match would make the code worse to read for no gain.
SELF = "tools/check_docs.py"


def _exists_exact(p: Path) -> bool:
    """`Path.exists()`, but case-sensitive on every platform.

    Plain `.exists()` makes this whole tool answer differently depending on who runs it. Windows and
    macOS are case-insensitive by default, so a doc whose casing does not match the file resolves
    locally and 404s for a reader on a case-sensitive filesystem -- and on GitHub, which serves blob
    paths case-sensitively regardless of the author's OS.

    That is not hypothetical: it is exactly how this function came to exist. A stale tree entry passed
    clean on Windows and failed in CI on Linux, so the local run was reporting OK over a real error.
    Comparing against the actual directory listing removes the platform from the answer.
    """
    try:
        if not p.exists():
            return False
        # Collapse `..` textually with normpath, NOT Path.resolve(). resolve() asks the filesystem for
        # the canonical path, and on Windows that hands back the on-disk casing -- which would quietly
        # re-mask the exact mismatch this function exists to find. Links arrive unresolved
        # (`packages/meic/../../docs/README.md`), so without this the walk below compares a literal
        # ".." against a directory listing and reports every relative link as broken.
        cur = Path(os.path.normpath(p))
        while cur != ROOT and cur.parent != cur:
            if cur.name not in {c.name for c in cur.parent.iterdir()}:
                return False
            cur = cur.parent
    except OSError:
        return False
    return True


#: Where the orchestrator declares its subcommands, and the doc that claims to list all of them.
_CLI_SOURCE = "packages/orchestrator/src/cherrypick/cli.py"
_CLI_DOC = "docs/orchestrator-cli.md"
_CLI_CHOICES = re.compile(r"choices=\[(.*?)\]", re.S)
_CLI_NAME = re.compile(r'"([a-z][a-z-]*)"')
#: A command row in the reference: `| `doctor` | what it does | flags |`.
_CLI_DOC_ROW = re.compile(r"^\|\s*`([a-z][a-z-]*)", re.M)


def _check_cli_coverage() -> list[str]:
    """`docs/orchestrator-cli.md` claims to document *every* command. Hold it to that.

    A one-directional check would be worth little; both directions fail in practice and for different
    reasons. A command added to `cli.py` without a doc row is invisible to anyone reading the
    reference, and a row left behind after a command is removed sends readers at something that now
    exits non-zero. Neither shows up in any other check here: both files are individually valid, and
    it is only the disagreement between them that is the defect.

    Verified by hand during a docs review at 27/27. That is exactly the kind of result that is true
    the day someone checks and quietly false a month later, which is the argument for automating it.
    """
    src, doc = ROOT / _CLI_SOURCE, ROOT / _CLI_DOC
    if not (src.exists() and doc.exists()):
        return []  # the layout moved; the tree/link rules will say so more usefully than this one
    block = _CLI_CHOICES.search(src.read_text(encoding="utf-8"))
    if not block:
        return [f"{_CLI_SOURCE}: no argparse `choices=[...]` block -- cli coverage cannot be checked"]
    real = set(_CLI_NAME.findall(block.group(1)))
    documented = set(_CLI_DOC_ROW.findall(doc.read_text(encoding="utf-8")))
    findings = []
    for missing in sorted(real - documented):
        findings.append(f"{_CLI_DOC}: command `{missing}` exists in cli.py but is not documented")
    for extra in sorted(documented - real):
        findings.append(f"{_CLI_DOC}: documents `{extra}`, which is not a cli.py command")
    return findings


#: The runbook table that claims to be the complete loopback-port inventory.
_PORT_DOC = "docs/operations.md"
#: Where each port is really decided. Deliberately an EXPLICIT table rather than a scan for
#: "integers that look like ports": a generic scan reports every timeout and byte count in the
#: repo, and a linter that cries wolf gets switched off (see this module's test file).
#: Each entry is (source path, regex); every capture group is read as a port.
#: Two servers survive the 2026-08-12 consolidation: the console (the one read surface) and the
#: settings editor (the one mutating surface). The five module dashboards and their embed ports went
#: with the code that served them.
_PORT_DECLS: tuple[tuple[str, str], ...] = (
    (
        "packages/orchestrator/src/cherrypick/orchestrator/settings_serve.py",
        r'scfg\.get\("port",\s*(\d+)\)',
    ),
    ("packages/console/server/src/config.ts", r'serve\["port"\]\s*:\s*(\d+)'),
)
#: Ports the table documents that no source declares, each with the reason it cannot. Every entry
#: carries a reason on purpose -- an allowlist without them is where drift hides.
_PORT_DOC_ONLY = {
    "7699": "MEIC's optional REST sidecar -- off by default, no module-level constant",
    "3306": "Dolt's own default -- an external service the suite only keeps alive",
}
#: A port cell in the runbook table: `| 8787 | Suite dashboard ... |`.
_PORT_ROW = re.compile(r"^\|\s*([\d\s/(+)-]+?)\s*\|", re.M)
_PORT_NUM = re.compile(r"\d{4}")


def _check_ports() -> list[str]:
    """The runbook's port table says "all loopback-only" and reads as a complete inventory. Hold it.

    The bug: the table said flies' dashboard was 8803 while `dashboard.py` said 5052, and the source
    comment three lines above that constant agreed with the *table*, so reading the code did not settle
    it either. Separately the table simply omitted four surfaces (console, scout, settings, the gex
    WebSocket) while presenting itself as complete -- and `/uninstall` uses it to decide what to stop,
    so those four kept running after an "uninstall".

    Most of those rows are gone now (2026-08-12: one read surface, one mutating one), which does not
    retire the rule -- the failure it catches is a table that reads complete and is not, and a short
    table is just as capable of that. It is the reason `/uninstall` can still be trusted to stop
    everything.

    Checks presence in both directions and nothing else. It cannot tell whether the description beside
    a port is right, and it cannot see a port that only ever arrives as a `--port` argument.
    """
    doc = ROOT / _PORT_DOC
    if not doc.exists():
        return []  # layout moved; the link rule will say so more usefully
    declared: dict[str, str] = {}
    for rel, pattern in _PORT_DECLS:
        src = ROOT / rel
        if not src.exists():
            return [f"{rel}: port source is missing -- the port check cannot run"]
        found = re.findall(pattern, src.read_text(encoding="utf-8"))
        flat = [g for match in found for g in (match if isinstance(match, tuple) else (match,))]
        if not flat:
            return [f"{rel}: no port matched the declared pattern -- the port check cannot run"]
        for port in flat:
            declared[port] = rel
    documented: set[str] = set()
    for cell in _PORT_ROW.findall(doc.read_text(encoding="utf-8")):
        documented.update(_PORT_NUM.findall(cell))

    findings = []
    for port in sorted(set(declared) - documented):
        findings.append(
            f"{_PORT_DOC}: port {port} is declared in {declared[port]} but missing from the port table"
        )
    for port in sorted(documented - set(declared) - set(_PORT_DOC_ONLY)):
        findings.append(
            f"{_PORT_DOC}: port table lists {port}, which no source declares -- "
            "fix the row, or allowlist it in _PORT_DOC_ONLY with the reason"
        )
    return findings


#: Every annotated config template in the suite. Discovered, not listed: a new package's template
#: should be covered the day it lands, not the day someone remembers to add it here.
_CONFIG_EXAMPLE_NAME = "config.example.json"
#: The orchestrator's, kept as a name for the tests that pin the original bug.
_CONFIG_EXAMPLE = "packages/orchestrator/config.example.json"
#: Files whose *text* counts as a mention. A key described in a module's CLAUDE.md but read by no
#: Python is not dead -- MEIC's loop is driven by an agent reading that very table, so `stop_type`
#: and the `orb_*` family have no Python reader and are entirely live. Scanning prose is what keeps
#: the rule from reporting 23 false deaths on that one package alone.
_MENTION_TEXT_SUFFIXES = {".md", ".ts", ".tsx"}


def _json_key_names(obj: object, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _json_key_names(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _json_key_names(v, out)


def _documented_config_keys(obj: object, out: set[str]) -> None:
    """Leaf key names from the example, minus its self-documentation.

    `_`-prefixed keys are the file's own `_note`/`_comment` prose and `*_header` keys are the section
    markers `settings --organize` inserts -- both are deliberate structure, not knobs, and `configedit`
    preserves them on every write.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not k.startswith("_") and not k.endswith("_header"):
                out.add(k)
                _documented_config_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _documented_config_keys(v, out)


def _check_dead_config_keys(files: list[Path]) -> list[str]:
    """A key documented in `config.example.json` that nothing anywhere in the suite mentions.

    The bug: `paper.install_argv`/`uninstall_argv` sat in the template under a note claiming
    install/uninstall retained them to delete the old task by name. Nothing read either one -- legacy
    deletion goes through `tasks.legacy_task_names`, which reads only `task_name` -- so the file
    documented a knob that did nothing *and* supplied a false reason for keeping it. The same scan then
    found `earnings.paper.entry_argv`/`exit_argv`, which `jobspec` ignores because the
    `cherrypick_scheduled` branch hardcodes the launcher verbs.

    "Mentioned" is deliberately generous: any string literal in any tracked `.py`, or any key in any
    other tracked `.json`. Keys reach code by routes a narrower scan misses -- `entry_task_name` is read
    by iterating a tuple of names, never as `.get("entry_task_name")`, and `entry_price_strategy` is a
    MEIC key that appears only in JSON and prose. Both looked dead to a `.get()`-only scan and are not.
    Erring toward silence is right here: this rule exists to catch a key with NO reader anywhere, and a
    false accusation of deadness invites deleting something load-bearing.

    So it checks one direction only. The reverse -- a key the code reads that the example omits -- is
    not mechanically checkable at useful precision: a scan of every `.get("...")` in the orchestrator
    returns 317 hits, nearly all of them ordinary dict access with no relationship to config.

    Runs over **every** `config.example.json` in the suite, not just the orchestrator's. The gap that
    prompted widening it: `exit_after_announcement_minutes` closes three earnings strategies four
    hours after entry and was absent from that package's template, so the default ran with nothing in
    config to explain it.
    """
    targets = [p for p in files if p.name == _CONFIG_EXAMPLE_NAME and p.exists()]
    if not targets:
        return []

    literals: set[str] = set()
    text_blobs: list[str] = []
    for path in files:
        if not path.exists() or path.name == _CONFIG_EXAMPLE_NAME:
            continue
        if path.suffix == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    literals.add(node.value)
        elif path.suffix == ".json":
            try:
                _json_key_names(json.loads(path.read_text(encoding="utf-8")), literals)
            except (json.JSONDecodeError, OSError):
                continue
        elif path.suffix in _MENTION_TEXT_SUFFIXES:
            text_blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    prose = "\n".join(text_blobs)

    findings = []
    for target in targets:
        rel = target.relative_to(ROOT).as_posix()
        try:
            example = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(f"{rel}: not valid JSON ({exc.msg}) -- the dead-key check cannot run")
            continue
        documented: set[str] = set()
        _documented_config_keys(example, documented)
        for key in sorted(documented - literals):
            if key in prose:  # described in a CLAUDE.md / doc / TS source -- a real consumer
                continue
            findings.append(
                f"{rel}: documents `{key}`, which nothing in the suite reads or documents -- "
                "remove it, or the note beside it is promising something that does not happen"
            )
    return findings


#: Where the AI insight's tool policy is really decided, and the name of the list it builds.
_TOOL_POLICY_SOURCE = "packages/orchestrator/src/cherrypick/orchestrator/eod_insight.py"
_TOOL_POLICY_NAME = "_DISALLOWED_TOOLS"
#: A doc restating that policy: `--disallowed-tools Bash Edit Write ...` (bare or backticked).
_TOOL_POLICY_DOC = re.compile(r"--disallowed-tools\s+([A-Za-z ]+?)\s*(?:`|--|\n|$)")


def _check_tool_policy(files: list[Path]) -> list[str]:
    """The docs' account of what the AI insight may do must match the code's.

    This is the only rule here guarding a **security** claim, and it exists because that claim was
    wrong in the worst direction. `docs/reporting-and-dashboard.md`, `docs/guardrails-and-modes.md`,
    and the module's own docstring all said the agent could not reach the network -- while
    `eod_insight.research_events` defaults to true and *grants* `WebSearch`. Every other rule here
    guards an inconvenience; a false "it cannot reach the network" is the kind of sentence somebody
    relies on when deciding whether to enable something.

    Checks the disallowed set both ways against `_DISALLOWED_TOOLS`, and separately refuses any doc
    that claims the run cannot reach the network while `WebSearch` is absent from that set (i.e. is
    grantable). It cannot check the prose around the flag, only the flag's contents -- so keep the
    surrounding sentence honest by hand.
    """
    src = ROOT / _TOOL_POLICY_SOURCE
    if not src.exists():
        return []
    real: set[str] | None = None
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError):
        return [f"{_TOOL_POLICY_SOURCE}: unparseable -- the tool-policy check cannot run"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == _TOOL_POLICY_NAME for t in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                real = {
                    e.value
                    for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
    if real is None:
        return [f"{_TOOL_POLICY_SOURCE}: no {_TOOL_POLICY_NAME} -- the tool-policy check cannot run"]

    findings = []
    for path in files:
        if path.suffix.lower() not in DOC_SUFFIXES or not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            m = _TOOL_POLICY_DOC.search(line)
            if not m:
                continue
            claimed = set(m.group(1).split())
            for extra in sorted(claimed - real):
                findings.append(
                    f"{rel}:{n}: claims `{extra}` is disallowed for the AI insight, but "
                    f"{_TOOL_POLICY_NAME} does not list it -- the run may still be granted it"
                )
            for missing in sorted(real - claimed):
                findings.append(
                    f"{rel}:{n}: omits `{missing}` from the disallowed list that {_TOOL_POLICY_NAME} "
                    "does contain"
                )
        if "WebSearch" not in real:
            for n, line in enumerate(text.split("\n"), 1):
                low = line.lower()
                if "reach the network" in low and not any(
                    w in low for w in ("does reach", "can reach", "by default", "exception")
                ):
                    findings.append(
                        f"{rel}:{n}: says the AI insight cannot reach the network, but `WebSearch` "
                        "is not in the always-disallowed set and is granted when "
                        "`eod_insight.research_events` is on (it defaults to on)"
                    )
    return findings


#: Every index that claims to enumerate the suite's packages.
_ROSTER_DOCS = ("docs/architecture.md", "docs/README.md", "CLAUDE.md", "README.md")


def _check_package_roster() -> list[str]:
    """A package missing from an index that claims to list them all.

    The bug: the suite grew from seven packages to ten, and four independent indexes each stopped at
    seven. `packages/console` -- 17,920 lines and the only non-Python package -- appeared in none of
    them, and `packages/desk`, the one surface that places live orders, appeared in none either, so a
    reader of the guardrails docs alone would conclude no sanctioned live-order path existed.

    Truth is the filesystem: a directory under `packages/` holding a `CLAUDE.md` is a package. The match
    is deliberately dumb -- the literal string `packages/<name>` anywhere in the file. It checks that a
    package is *mentioned*, never that what is said about it is any good.
    """
    pkg_root = ROOT / "packages"
    if not pkg_root.is_dir():
        return []
    names = sorted(p.name for p in pkg_root.iterdir() if (p / "CLAUDE.md").is_file())
    findings = []
    for rel in _ROSTER_DOCS:
        doc = ROOT / rel
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        for name in names:
            if f"packages/{name}" not in text:
                findings.append(f"{rel}: does not mention packages/{name}, but it is a package")
    return findings


def _real_modules() -> set[str]:
    """Every importable `cherrypick.*` dotted name, derived from the filesystem.

    Deliberately not `importlib.util.find_spec`: the `docs` CI job is a bare checkout plus Python and
    installs no packages, so an import-based check would report every module missing there. Walking the
    tree also sidesteps per-package layout differences for free -- `packages/core` is flat while the
    other six are src-layout, and `cherrypick.cli` sits a level above `cherrypick.orchestrator.*`.
    """
    names: set[str] = set()
    roots = [ROOT / "packages/core/cherrypick"]
    pkgs = ROOT / "packages"
    if pkgs.is_dir():
        roots += [p / "src/cherrypick" for p in pkgs.iterdir() if (p / "src/cherrypick").is_dir()]
    for base in roots:
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            parts = list(f.relative_to(base.parent).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                names.add(".".join(parts))
    return names


def _check_commands(rel: str, lines: list[str], modules: set[str]) -> list[str]:
    """Verify that documented commands still point at something real.

    This closes the class of bug that survived every earlier sweep: a module path embedded in
    something that does not look like a path. Three commands launched
    `Start-Process python -ArgumentList 'src\\streamer.py'` for weeks after the namespace migration
    moved that file, because sweeps matched `src/<mod>.py` and not a backslashed bare filename inside
    a PowerShell argument list. Two deleted slash commands had the same shape.

    Two checks, both scoped to lines inside a fence that actually invoke something:

    * `-m cherrypick.…` must name a real module. This is the form the whole suite now uses, so it is
      the highest-value thing to keep honest.
    * A script path *containing a separator* must resolve against the doc's own directory, its package
      root, or the repo root -- whichever the reader would plausibly be sitting in. Bare filenames are
      excluded on purpose; `python run.py` is meaningful only next to the prose telling you where to
      run it, and would fire on every one of the 56 legitimate uses.
    """
    findings: list[str] = []
    parts = rel.split("/")
    pkg_root = ROOT / parts[0] / parts[1] if len(parts) > 2 and parts[0] == "packages" else ROOT
    bases = [(ROOT / rel).parent, pkg_root, ROOT]

    in_fence = False
    for n, line in enumerate(lines, 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence or not _CMD_LINE.search(line):
            continue

        for dotted in _DASH_M.findall(line):
            if dotted.endswith(".") or any(t in dotted for t in _PLACEHOLDER):
                continue  # `-m cherrypick.meic.<mod>` names a shape, not a module
            if dotted not in modules:
                findings.append(f"{rel}:{n}: `-m {dotted}` is not a real module")

        for token in _CMD_PATH.findall(line):
            if any(t in token for t in _PLACEHOLDER) or token.startswith(("http", "~")):
                continue
            if not any(_exists_exact(b / token) for b in bases):
                findings.append(
                    f"{rel}:{n}: command references {token!r}, which resolves from neither this "
                    "file's directory, its package root, nor the repo root"
                )
    return findings


def _check_trees(rel: str, lines: list[str]) -> list[str]:
    """Resolve every path drawn in a fenced ASCII file-layout tree, and report the ones that vanished.

    Scoped tightly on purpose, because a tree diagram is a loose format and over-reach here would be
    worse than no check at all:

    * Only fenced blocks whose root line ends in `/` are treated as file layouts. `reporting-and-
      dashboard.md` draws a *dataflow* with the same box characters (`report.run(session=day) ──►`);
      its root is an expression, not a directory, so it is skipped rather than nonsensically resolved.
    * Roots outside the repo (`~/.cherrypick/data/meic/`) are skipped -- runtime state, not the tree.
    * A row is only judged when its parent directory actually exists. `logs/` is gitignored and absent
      from a fresh checkout, so its children are unknowable, not wrong.
    * Existence is checked on disk, not against git. `.claude/settings.json` is gitignored but real,
      and a tree may legitimately draw it.
    """
    findings: list[str] = []
    in_fence = False
    stack: list[tuple[int, str]] = []  # (indent column, directory name)
    base: Path | None = None
    prefix = ""

    for n, line in enumerate(lines, 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            stack, base, prefix = [], None, ""
            continue
        if not in_fence:
            continue

        row = _TREE_ROW.match(line)
        if not row:
            # The root line: the last non-empty line before the first branch, ending in a slash.
            text = line.strip()
            if text.endswith("/") and not text.startswith(("~", "/")) and "://" not in text:
                root = text.rstrip("/")
                # The root line usually names the repo itself ("cherrypick/"), which is not a
                # directory *inside* the repo -- and need not match the checkout's folder name (this
                # one is still cloned as "cherrypick-next"). Treat it as a prefix only when it really
                # resolves to a directory; otherwise it denotes the repo root.
                base, prefix = ROOT, root if (ROOT / root).is_dir() else ""
                stack = []
            continue

        if base is None:  # a tree we chose not to interpret (e.g. the dataflow diagram)
            continue

        col, name = len(row.group("indent")), row.group("name")
        while stack and stack[-1][0] >= col:
            stack.pop()

        if any(tok in name for tok in _PLACEHOLDER):
            if name.endswith("/"):
                stack.append((col, name.rstrip("/")))
            continue

        parts = [prefix] if prefix else []
        parts += [d for _, d in stack] + [name.rstrip("/")]
        target = base.joinpath(*parts)

        if name.endswith("/"):
            stack.append((col, name.rstrip("/")))
        comment = line.split("#", 1)[1].lower() if "#" in line else ""
        if any(mark in comment for mark in _RUNTIME_MARKERS):
            continue
        if target.parent.exists() and not _exists_exact(target):
            findings.append(f"{rel}:{n}: file-layout tree lists {'/'.join(parts)!r}, which does not exist")
    return findings


def check(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    modules = _real_modules()
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel == SELF:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        if not _is_test(rel):
            for n, line in enumerate(lines, 1):
                if any(not _is_quoted_example(line, m.start()) for m in _ABS_PATH.finditer(line)):
                    findings.append(
                        f"{rel}:{n}: absolute machine path -- derive it from "
                        "Path(__file__), an env var, or config"
                    )
                if any(not _is_quoted_example(line, m.start()) for m in _HOME_POINTER.finditer(line)):
                    findings.append(
                        f"{rel}:{n}: points outside the repo (~/.claude/) -- move it "
                        "in-repo, or mark it non-authoritative"
                    )
                for m in _ACCOUNT.finditer(line):
                    if _is_quoted_example(line, m.start()):
                        continue
                    findings.append(
                        f"{rel}:{n}: possible unmasked account number {m.group(1)!r} -- mask to ****1234"
                    )
                    break

        if path.suffix.lower() in DOC_SUFFIXES:
            for n, line in enumerate(lines, 1):
                for target in _MD_LINK.findall(line):
                    t = target.split("#", 1)[0].strip()
                    if not t or t.startswith(("http://", "https://", "mailto:", "<")):
                        continue
                    if not _exists_exact(path.parent / t):
                        findings.append(f"{rel}:{n}: broken relative link -> {t}")
                if m := _MD_LINK_OPEN.search(line):
                    findings.append(
                        f"{rel}:{n}: unclosed markdown link -> '({m.group(1)}' is missing its ')' "
                        "(it renders as literal text, and the broken-link check cannot see it)"
                    )
            findings += _check_trees(rel, lines)
            findings += _check_commands(rel, lines, modules)
    return findings


def main() -> int:
    tracked = tracked_files()
    findings = (
        check(tracked)
        + _check_cli_coverage()
        + _check_ports()
        + _check_dead_config_keys(tracked)
        + _check_package_roster()
        + _check_tool_policy(tracked)
    )
    if not findings:
        print("check_docs: OK")
        return 0
    for f in sorted(findings):
        print(f)
    print(f"\ncheck_docs: {len(findings)} finding(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
