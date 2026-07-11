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
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import secrets

_ROOT = Path(__file__).resolve().parent.parent
_LOG = _ROOT / "logs" / "notify.log"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Notifier:
    def __init__(self, notify_cfg: dict[str, Any] | None = None):
        cfg = notify_cfg or {}
        self.channels = cfg.get("channels", ["log"])
        self.app_name = cfg.get("desktop_app_name", "Cherrypick")

    # -- the floor -----------------------------------------------------------------
    def _write_log(self, level: str, key: str, title: str, message: str) -> None:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
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

    # -- push channels (best-effort) -----------------------------------------------
    def _push_desktop(self, level: str, title: str, message: str) -> dict[str, Any]:
        if os.name != "nt":
            return {"ok": False, "skipped": "desktop notifications are Windows-only"}
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
            )
            return {"ok": True}
        except Exception as exc:  # never let a push failure escape
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        # Discord (behind Cloudflare) rejects the default "Python-urllib" User-Agent with 403, so send
        # an explicit one. Harmless for Slack.
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Cherrypick-Notifier/1.0 (+https://github.com/cherrypick)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                return {"ok": 200 <= resp.status < 300, "status": resp.status}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _push_slack(self, level: str, title: str, message: str) -> dict[str, Any]:
        url = secrets.get_webhook("slack")
        if not url:
            return {
                "ok": False,
                "skipped": "slack webhook not set (run: cherrypick secrets-set --channel slack)",
            }
        return self._post_json(url, {"text": f"[{level}] {self.app_name} — {title}\n{message}"})

    def _push_discord(self, level: str, title: str, message: str) -> dict[str, Any]:
        url = secrets.get_webhook("discord")
        if not url:
            return {
                "ok": False,
                "skipped": "discord webhook not set (run: cherrypick secrets-set --channel discord)",
            }
        # Discord caps `content` at 2000 chars; keep well under with a margin for the prefix.
        body = f"**[{level}] {self.app_name} — {title}**\n{message}"[:1900]
        return self._post_json(url, {"content": body})

    # -- public --------------------------------------------------------------------
    def notify(self, level: str, key: str, title: str, message: str) -> dict[str, Any]:
        """Emit a notification. Always writes the log floor first, then any push channels."""
        level = level.upper()
        self._write_log(level, key, title, message)  # the guarantee
        results: dict[str, Any] = {"log": {"ok": True}}
        for ch in self.channels:
            if ch == "log":
                continue
            try:  # a push channel must never break the caller (the watchdog runs on this path)
                if ch == "desktop":
                    results["desktop"] = self._push_desktop(level, title, message)
                elif ch == "slack":
                    results["slack"] = self._push_slack(level, title, message)
                elif ch == "discord":
                    results["discord"] = self._push_discord(level, title, message)
                else:
                    results[ch] = {"ok": False, "skipped": f"unknown channel '{ch}'"}
            except Exception as exc:
                results[ch] = {"ok": False, "error": str(exc)}
        return results


def notify(
    notify_cfg: dict[str, Any] | None, level: str, key: str, title: str, message: str
) -> dict[str, Any]:
    """Module-level convenience: construct a Notifier and emit one notification."""
    return Notifier(notify_cfg).notify(level, key, title, message)


if __name__ == "__main__":  # `python notify/notifier.py "message"` fires a test notification
    msg = sys.argv[1] if len(sys.argv) > 1 else "Cherrypick notification test"
    res = Notifier({"channels": ["log", "desktop"]}).notify("INFO", "test", "Test", msg)
    print(json.dumps(res, indent=2))
