"""Notification dispatch: logging floor + best-effort push channels.

Channels:
  - "log"     : always on, the floor. Structured NOTIFY line to logs/notify.log.
  - "desktop" : Windows tray balloon via a short-lived PowerShell process (best-effort).
  - "slack"   : POST to an Incoming Webhook whose URL is stored in the OS keyring (see notify.secrets;
                set via `cherrypick secrets-set --channel slack`). Never in config/files/env vars.
  - "discord" : POST to a Discord Incoming Webhook whose URL is stored in the OS keyring
                (`cherrypick secrets-set --channel discord`). Never in config/files/env vars.

No push channel may raise; failures are swallowed after the floor has been written. This module
uses only the stdlib + the OS shell — no MCP, no third-party client — so it is safe to call from
the watchdog (which must have no network/AI failure mode on its own reliability path).

**Every webhook send in the suite goes through `send_webhook`** — this class's pushes, the
QuikOptions series, the flies payoff chart, and anything a person or a session posts by hand
(`run.py notify-send`). Each send, landed or not, appends one record to
`data/outbound/YYYY-MM.jsonl`: what was sent (text, card fields, attachment fingerprints), where
(the webhook's name, never its URL), the message id Discord returns, and fingerprints of the files
the message was built from. `run.py sent` reads it back: `--stale` names a post whose inputs have
changed since it went out, `--verify` asks Discord whether the message still exists. Recording is
best-effort like every push: it can never stop or fail a send.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import secrets


def _default_log_dir() -> Path:
    """cherrypick's per-user logs home (~/.cherrypick/logs, or CHERRYPICK_HOME/logs). Kept in sync with
    config.LOGS_DIR but computed independently so the notifier — which sits on the reliability path —
    stays free of a config import. Previously this wrote notify.log *inside the source tree*, which both
    leaked logs into the repo and put them where the dashboard (which reads config.LOGS_DIR) never
    looked; anchoring both at the user home fixes that mismatch."""
    return _home.home() / "logs"  # core.home is stdlib-only, so the reliability path stays light


_LOG = _default_log_dir() / "notify.log"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- the outbound record ---------------------------------------------------------------------------
# Discord (behind Cloudflare) rejects the default "Python-urllib" User-Agent with 403, so every send
# carries an explicit one. Harmless for Slack.
USER_AGENT = "cherrypick-notifier/1.0 (+https://github.com/cherrypick)"
_REPO = Path(__file__).resolve().parents[5]  # packages/orchestrator/src/cherrypick/notify -> root


def outbound_dir() -> Path:
    """Under data/, not logs/: the log archive compresses and moves logs, and this record has to
    stay readable for as long as a post can be questioned."""
    return _home.home() / "data" / "outbound"


def fingerprint(path: str | Path) -> dict[str, Any]:
    """A file as it stands now: its sha256 and modification time, or that it is missing."""
    p = Path(path)
    try:
        data = p.read_bytes()
        mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return {"path": str(p), "missing": True}
    return {"path": str(p), "sha256": hashlib.sha256(data).hexdigest(), "mtime": mtime}


def _code_version() -> str | None:
    """The checkout's commit, read from .git directly: no subprocess on the reliability path."""
    try:
        git = _REPO / ".git"
        if git.is_file():  # a worktree: `gitdir: <path>`
            git = Path(git.read_text(encoding="utf-8").split(":", 1)[1].strip())
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head[:12]
        ref = head[5:]
        common = (
            Path((git / "commondir").read_text(encoding="utf-8").strip())
            if (git / "commondir").exists()
            else None
        )
        for root in (git, (git / common) if common else None):
            if root is not None and (root / ref).exists():
                return (root / ref).read_text(encoding="utf-8").strip()[:12]
            if root is not None and (root / "packed-refs").exists():
                for line in (root / "packed-refs").read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split(" ", 1)[0][:12]
    except (OSError, IndexError, ValueError):
        pass
    return None


def record_outbound(entry: dict[str, Any]) -> None:
    """Append one send to this month's record. Never raises: a record that cannot be written costs
    the record, never the send."""
    try:
        folder = outbound_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{entry['ts'][:7]}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 -- the record is best-effort, like every push
        pass


def _multipart(payload: dict, files: list[Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n',
        json.dumps(payload).encode(),
    ]
    for i, path in enumerate(files):
        parts += [
            f"\r\n--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="files[{i}]"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            path.read_bytes(),
        ]
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _waiting(url: str) -> str:
    """Discord answers `?wait=true` with the message it created, so the record gets its id."""
    return url + ("&" if "?" in url else "?") + "wait=true"


def send_webhook(
    url: str,
    payload: dict[str, Any],
    files: Iterable[str | Path] = (),
    *,
    channel: str,
    source: str,
    kind: str,
    session: str | None = None,
    refers_to: str | None = None,
    inputs: Iterable[str | Path] = (),
    timeout: float = 20,
    opener=None,
) -> dict[str, Any]:
    """POST one message to a webhook and record it. Returns {"ok", "status", "message_id", "error"}.

    `channel` is the webhook's keyring name (`discord`, `discord_reporting`, `slack`), never its URL.
    `source` names the sender (a job id or a notification key), `kind` what it is (`notify`,
    `digest`, `report`, `payoff`, `correction`, `note`). `inputs` are the files the message was built
    from; their fingerprints at send time are what `run.py sent --stale` compares against. A Discord
    429 is waited out once, for the time it asks (at most 30 s), then retried."""
    opener = opener or urllib.request.urlopen
    files = [Path(f) for f in files]
    discord = channel.startswith("discord")
    target = _waiting(url) if discord else url
    if files:
        body, ctype = _multipart(payload, files)
    else:
        body, ctype = json.dumps(payload).encode("utf-8"), "application/json"
    result: dict[str, Any] = {"ok": False, "status": None, "message_id": None, "error": None}
    for attempt in (1, 2):
        req = urllib.request.Request(
            target, data=body, headers={"Content-Type": ctype, "User-Agent": USER_AGENT}
        )
        try:
            with opener(req, timeout=timeout) as resp:
                result["status"] = resp.status
                result["ok"] = 200 <= resp.status < 300
                if not result["ok"]:
                    result["error"] = f"HTTP {resp.status}"
                try:
                    raw = resp.read() if hasattr(resp, "read") else b""
                    result["message_id"] = (json.loads(raw or b"{}") or {}).get("id")
                except (ValueError, AttributeError, OSError):
                    pass
            break
        except urllib.error.HTTPError as exc:
            result["status"] = exc.code
            if exc.code == 429 and attempt == 1:
                try:
                    wait = float(json.loads(exc.read().decode() or "{}").get("retry_after", 5))
                except (ValueError, AttributeError):
                    wait = 5.0
                time.sleep(min(max(wait, 0.5), 30.0))
                continue
            result["error"] = f"HTTP {exc.code}"
            break
        except Exception as exc:  # noqa: BLE001 -- a failed send is reported, never raised
            result["error"] = f"{type(exc).__name__}: {exc}"
            break
    try:
        attachments = [
            {"name": f.name, **{k: v for k, v in fingerprint(f).items() if k != "path"}} for f in files
        ]
        record_outbound(
            {
                "ts": _utcnow(),
                "channel": channel,
                "source": source,
                "kind": kind,
                "session": session,
                "refers_to": refers_to,
                **result,
                "content": payload.get("content") if discord else payload.get("text"),
                "embeds": payload.get("embeds"),
                "attachments": attachments,
                "inputs": [fingerprint(p) for p in inputs],
                "code": _code_version(),
            }
        )
    except Exception:  # noqa: BLE001 -- never let the record cost the send's result
        pass
    return result


def read_outbound(
    session: str | None = None, since: str | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    """Every recorded send, oldest first, narrowed to a session, a UTC start time and a kind."""
    out: list[dict[str, Any]] = []
    folder = outbound_dir()
    for path in sorted(folder.glob("????-??.jsonl")) if folder.exists() else []:
        if since and path.stem < since[:7]:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if session and entry.get("session") != session:
                continue
            if since and str(entry.get("ts", "")) < since:
                continue
            if kind and entry.get("kind") != kind:
                continue
            out.append(entry)
    return out


def changed_inputs(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The inputs whose file is not what it was when the message was sent: changed, gone, or back."""
    changed = []
    for then in entry.get("inputs") or []:
        now = fingerprint(then["path"])
        if now.get("missing") != then.get("missing") or now.get("sha256") != then.get("sha256"):
            changed.append({"path": then["path"], "then": then, "now": now})
    return changed


def verify_message(entry: dict[str, Any], opener=None) -> str:
    """Whether Discord still holds the sent message: `present`, `deleted`, or why it is unknown."""
    if not str(entry.get("channel", "")).startswith("discord"):
        return "unknown: not a Discord send"
    if not entry.get("message_id"):
        return "unknown: no message id recorded"
    url = secrets.read_entry(entry["channel"])
    if not url or url is secrets.KEYRING_UNAVAILABLE:
        return f"unknown: no {entry['channel']} webhook readable"
    req = urllib.request.Request(
        f"{str(url).split('?', 1)[0]}/messages/{entry['message_id']}", headers={"User-Agent": USER_AGENT}
    )
    try:
        with (opener or urllib.request.urlopen)(req, timeout=10) as resp:
            return "present" if 200 <= resp.status < 300 else f"unknown: HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return "deleted" if exc.code == 404 else f"unknown: HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"unknown: {type(exc).__name__}"


def _windows_session_id() -> int | None:
    """This process's Windows session (0 = services, no desktop), or None off Windows / unknown."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        sid = wintypes.DWORD()
        k32 = ctypes.WinDLL("kernel32")
        if k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid)):
            return int(sid.value)
    except Exception:  # noqa: BLE001 -- unknown is "not session 0": try the toast as before
        return None
    return None


def _applescript_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def desktop_argv(platform: str, level: str, title: str, message: str, which) -> list[str] | None:
    """The desktop-notification command off Windows, or None when this host has none (pure; `which`
    is `shutil.which`, faked in tests). Added in the 2026-10-08 OS audit: the channel was Windows-only,
    so on a Mac or a Linux desktop it was silently skipped.

    - macOS: `osascript -e 'display notification ...'` (always present). Strings go in as AppleScript
      literals, in an argv list, so nothing reaches a shell.
    - Linux: `notify-send` (libnotify), with CRITICAL urgency for WARN/CRITICAL so it stays up. It
      needs the session bus, which the cron lines carry.
    """
    if platform == "darwin":
        script = f"display notification {_applescript_str(message)} with title {_applescript_str(title)}"
        return ["osascript", "-e", script]
    if platform.startswith("linux") and which("notify-send"):
        urgency = "critical" if level in ("WARN", "CRITICAL") else "normal"
        return ["notify-send", "-u", urgency, title, message]
    return None


class Notifier:
    def __init__(self, notify_cfg: dict[str, Any] | None = None):
        cfg = notify_cfg or {}
        self.channels = cfg.get("channels", ["log"])
        self.app_name = cfg.get("desktop_app_name", "cherrypick")

    # -- the floor -----------------------------------------------------------------
    @staticmethod
    def _rotate_if_large(path, max_bytes: int = 5_000_000, keep: int = 3) -> None:
        # Same size-based rotation as orchestrator.util.rotate_if_large, inlined: this
        # package is deliberately stdlib-only with no orchestrator import (it sits on the
        # reliability path), and without rotation notify.log grew without bound —
        # logrotate refuses active .log files by design. Best-effort.
        try:
            if not path.exists() or path.stat().st_size < max_bytes:
                return
            for i in range(keep - 1, 0, -1):
                src = path.with_name(f"{path.name}.{i}")
                if src.exists():
                    os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
            os.replace(path, path.with_name(f"{path.name}.1"))
        except OSError:
            pass

    def _write_log(self, level: str, key: str, title: str, message: str) -> None:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_large(_LOG)
        line = json.dumps(
            {
                "ts": _utcnow(),
                "kind": "NOTIFY",
                "level": level,
                "key": key,
                "title": title,
                "message": message,
            }
        )
        with _LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def record(self, level: str, key: str, title: str, message: str) -> dict[str, Any]:
        """Write the log floor and push nothing -- for a record worth keeping whole but not worth a
        push of its own (an earnings rejection, which is summarised instead)."""
        level = level.upper()
        return {"log": self._write_log_safe(level, key, title, message)}

    def _write_log_safe(self, level: str, key: str, title: str, message: str) -> dict[str, Any]:
        """The floor, best-effort. It used to raise straight through `notify()` BEFORE any push was
        tried, so a full disk or a locked log file silenced every channel (2026-10-08 audit)."""
        try:
            self._write_log(level, key, title, message)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001 -- the floor failing must not take the pushes with it
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # -- push channels (best-effort) -----------------------------------------------
    def _push_desktop(self, level: str, title: str, message: str) -> dict[str, Any]:
        if os.name != "nt":
            argv = desktop_argv(sys.platform, level, f"{self.app_name}: {title}", message, shutil.which)
            if argv is None:
                return {"ok": False, "skipped": f"no desktop notifier on {sys.platform} (notify-send?)"}
            try:
                subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0)
                return {"ok": True}
            except Exception as exc:  # never let a push failure escape
                return {"ok": False, "error": str(exc)}
        if _windows_session_id() == 0:
            # Session 0 is where services run (the optional `run.py service` mode): it has no desktop,
            # so a balloon there reaches no one. Say so rather than report a toast nobody saw.
            return {"ok": False, "skipped": "no desktop in session 0 (running as a Windows service)"}
        icon = "Warning" if level in ("WARN", "CRITICAL") else "Info"
        safe_title = f"{self.app_name}: {title}"
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            f"$n.ShowBalloonTip(8000, {json.dumps(safe_title)}, {json.dumps(message)}, "
            f"[System.Windows.Forms.ToolTipIcon]::{icon});"
            "Start-Sleep -Seconds 9; $n.Dispose();"
        )
        encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-EncodedCommand",
                    encoded,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # -WindowStyle Hidden alone still creates the console before hiding it — a visible
                # flash on every toast when the parent (pythonw watchdog/trade-notify) has no console.
                # Defined inline: this module deliberately imports nothing from cherrypick.orchestrator.
                creationflags=0x08000000 if os.name == "nt" else 0,  # CREATE_NO_WINDOW
            )
            return {"ok": True}
        except Exception as exc:  # never let a push failure escape
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any], **record: Any) -> dict[str, Any]:
        """The one seam every push crosses: `send_webhook`, which sends and records it."""
        out = send_webhook(url, payload, timeout=6, **record)
        return {"ok": out["ok"], "status": out["status"], **({"error": out["error"]} if out["error"] else {})}

    def _push_slack(self, level: str, title: str, message: str, **record: Any) -> dict[str, Any]:
        url = secrets.get_webhook("slack")
        if not url:
            return {
                "ok": False,
                "skipped": "slack webhook not set (run: cherrypick secrets-set --channel slack)",
            }
        return self._post_json(
            url, {"text": f"[{level}] {self.app_name} — {title}\n{message}"}, channel="slack", **record
        )

    def _push_discord(
        self,
        level: str,
        title: str,
        message: str,
        embed: dict | None = None,
        **record: Any,
    ) -> dict[str, Any]:
        url = secrets.get_webhook("discord")
        if not url:
            return {
                "ok": False,
                "skipped": "discord webhook not set (run: cherrypick secrets-set --channel discord)",
            }
        payload: dict[str, Any]
        if embed:
            # An embed carries its own author/title/fields, so it needs no text alongside it. The
            # `[LEVEL] app — title` prefix a plain message gets is dropped here — in a channel
            # dedicated to this kind of push it's the same characters on every message, saying
            # nothing the card doesn't already say better.
            payload = {"embeds": [embed]}
        else:
            # Discord caps `content` at 2000 chars; keep well under with a margin for the prefix.
            payload = {"content": f"**[{level}] {self.app_name} — {title}**\n{message}"[:1900]}
        return self._post_json(url, payload, channel="discord", **record)

    # -- public --------------------------------------------------------------------
    def notify(
        self,
        level: str,
        key: str,
        title: str,
        message: str,
        embed: dict | None = None,
        *,
        kind: str = "notify",
        session: str | None = None,
        inputs: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        """Emit a notification. Always writes the log floor first, then any push channels.

        `embed` is a Discord-only enrichment (a colored card), ignored everywhere else. `message`
        stays the canonical text: it is what the log floor records and what every non-Discord
        channel receives, so a channel that can't render a card loses nothing but layout.

        `kind`, `session` and `inputs` go to the outbound record of each webhook push, with `key` as
        its source (see `send_webhook`).
        """
        level = level.upper()
        title, message = _portable(title), _portable(message)
        results: dict[str, Any] = {"log": self._write_log_safe(level, key, title, message)}
        record = {"source": key, "kind": kind, "session": session, "inputs": list(inputs)}
        for ch in self.channels:
            if ch == "log":
                continue
            try:  # a push channel must never break the caller (the watchdog runs on this path)
                if ch == "desktop":
                    results["desktop"] = self._push_desktop(level, title, message)
                elif ch == "slack":
                    results["slack"] = self._push_slack(level, title, message, **record)
                elif ch == "discord":
                    results["discord"] = self._push_discord(level, title, message, embed=embed, **record)
                else:
                    results[ch] = {"ok": False, "skipped": f"unknown channel '{ch}'"}
            except Exception as exc:
                results[ch] = {"ok": False, "error": str(exc)}
        return results


def _portable(text: str) -> str:
    """The user's home folder as `~` -- a pushed message never carries a username (the suite's
    portable-paths rule; a backup failure and a reconcile timeout both printed full paths to Discord,
    2026-10-08). Covers both slash directions and the doubled backslashes of a repr()'d path."""
    home = os.path.expanduser("~")
    if not text or not home or home == "~":
        return text
    for form in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        text = text.replace(form, "~")
    return text


def delivered(results: dict[str, Any]) -> bool:
    """Did a `notify()` reach anyone? True when any push channel succeeded, or -- for a log-only
    setup, where the floor IS the delivery -- when the floor was written. False when every push
    channel failed or was skipped: the caller must not record the message as sent (2026-10-08 audit:
    every caller stamped "notified" whatever happened, so a Discord outage lost alerts for good)."""
    pushes = {ch: r for ch, r in results.items() if ch != "log"}
    if not pushes:
        return bool((results.get("log") or {}).get("ok"))
    return any((r or {}).get("ok") for r in pushes.values())


def notify(
    notify_cfg: dict[str, Any] | None,
    level: str,
    key: str,
    title: str,
    message: str,
    embed: dict | None = None,
    **record: Any,
) -> dict[str, Any]:
    """Module-level convenience: construct a Notifier and emit one notification."""
    return Notifier(notify_cfg).notify(level, key, title, message, embed=embed, **record)


if __name__ == "__main__":  # `python notify/notifier.py "message"` fires a test notification
    msg = sys.argv[1] if len(sys.argv) > 1 else "cherrypick notification test"
    res = Notifier({"channels": ["log", "desktop"]}).notify("INFO", "test", "Test", msg)
    print(json.dumps(res, indent=2))
