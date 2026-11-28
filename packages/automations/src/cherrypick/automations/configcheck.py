"""Would the paper loop refuse this config? Answered before the session, not at its first tick.

The loop loads the config and resolves every automation (`config.load_arms`) on each tick and refuses to
start when that raises. `check` does the same load and reports the refusal, and touches nothing else: no
ledger, no stream request, no cache. It reads the config the loop would read, or candidate text (a save
not yet written), so a save can be refused for what would stop the loop.
"""

from __future__ import annotations

import json

from cherrypick.core import home as _home

from . import config as config_mod


def check(path: str | None = None, text: str | None = None) -> dict:
    """{"ok": True, "path"} when the loop would load the config, else {"ok": False, "error", "path"}.

    `text` is a candidate for the file the loop reads (`path` or the usual precedence), checked in its
    place. The error is the loop's own words (`arms: ...`)."""
    source = _home.module_config_source("automations", config_mod._PKG_ROOT, path)
    shown = str(source) if source is not None else None
    try:
        if text is None:
            config = config_mod.load_config(path)
        else:
            config = json.loads(text)
            if not isinstance(config, dict):
                raise ValueError("config: the file must hold a JSON object")
    except SystemExit as exc:
        return {"ok": False, "error": f"config: {exc}", "path": shown}
    except (OSError, ValueError) as exc:  # a JSON decode error is a ValueError
        return {"ok": False, "error": f"config: {exc}", "path": shown}
    try:
        config_mod.load_arms(config)
    except ValueError as exc:
        return {"ok": False, "error": f"arms: {exc}", "path": shown}
    except Exception as exc:  # anything else the loop would also die on
        return {"ok": False, "error": f"arms: {type(exc).__name__}: {exc}", "path": shown}
    return {"ok": True, "path": shown}
