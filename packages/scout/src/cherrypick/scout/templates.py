"""Tiny `string.Template` loader for htmx partials -- no jinja2 unless templating actually grows
(the plan's own call, revisit if/when a partial needs loops/conditionals beyond string-building in
Python and substituting the result)."""

from __future__ import annotations

from pathlib import Path
from string import Template

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def render(name: str, **context: str) -> str:
    tpl = Template((TEMPLATES_DIR / name).read_text(encoding="utf-8"))
    return tpl.safe_substitute(**context)
