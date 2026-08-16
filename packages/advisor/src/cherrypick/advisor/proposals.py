"""Turning a model's reply into typed proposals — tolerantly located, strictly parsed.

Two different postures, on purpose:

* **Locating** the JSON is tolerant. Models wrap JSON in prose, in code fences, in an apology. The
  slice from the first ``{`` to the last ``}`` recovers the payload from all of those, and costs
  nothing when the reply is clean.
* **Reading** it is strict. Once located, it is `json.loads` and a taxonomy check — no coercion, no
  guessing what a malformed proposal meant. A proposal this module cannot read is recorded as
  rejected with the reason, never silently dropped: a rejection the model can see next session is
  worth more than a clean-looking checkpoint.

The taxonomy is closed. Five kinds, each with required fields; anything else is `unknown_kind`.
Admission — bounds, caps, whether the module even accepts advice — is not decided here. This module
answers "is this a well-formed proposal of a kind we know", and nothing about whether it is allowed.
"""

from __future__ import annotations

import json
from typing import Any

KINDS = ("bounded_adjustment", "experiment_spec", "tune", "creative", "verdict")

# kind -> the fields that must be present and non-empty for the proposal to be readable at all.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "bounded_adjustment": ("module", "params"),
    "experiment_spec": ("module", "name", "params"),
    "tune": ("experiment_id", "params"),
    "creative": ("title",),
    "verdict": ("experiment_id", "recommendation"),
}

RECOMMENDATIONS = ("keep", "kill", "promote")


class ParseError(ValueError):
    """The reply held no readable JSON object at all."""


def locate_json(raw: str) -> dict[str, Any]:
    """Recover the JSON object from a model reply. Raises :class:`ParseError` if there is none."""
    if not raw or not raw.strip():
        raise ParseError("empty reply")
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ParseError("no JSON object found in reply")
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ParseError(f"reply JSON did not parse: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("reply JSON is not an object")
    return payload


def _is_single_entry(value: Any) -> bool:
    """The third shape a model reaches for: ONE ``{"param": ..., "value": ...}`` entry sent bare
    instead of wrapped in a list.

    Read as the ``{param: value}`` map it superficially resembles, it becomes params literally named
    ``param``, ``value`` and ``rationale`` — which the bounds check then refuses with
    ``param 'param' not in advice_bounds``, a reason that describes nothing the model did wrong and
    so teaches it nothing. The two shapes are told apart with certainty rather than by preference: a
    map reading would require a module to have declared a bound on a key called ``param``, and a
    bound is a strategy parameter name, so that key cannot exist. This is an ambiguity about shape,
    not about meaning, and resolving it belongs with the tolerant locating rather than the strict
    read.
    """
    return isinstance(value, dict) and isinstance(value.get("param"), str) and bool(value["param"])


def _as_params(value: Any) -> tuple[dict[str, Any], str | None]:
    """Both shapes the contract allows: the list-of-objects the prompt asks for, and the plain
    ``{param: value}`` map a model reaches for anyway. Anything else is a rejection reason."""
    if _is_single_entry(value):
        value = [value]
    if isinstance(value, dict):
        return dict(value), None
    if isinstance(value, list):
        out: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, dict) or "param" not in item:
                return {}, "params entries must be objects with a 'param' key"
            if item["param"] in out:
                return {}, f"duplicate param {item['param']!r}"
            out[str(item["param"])] = item.get("value")
        return out, None
    return {}, "params must be a list of {param, value} or a {param: value} object"


def _rationales(value: Any) -> dict[str, str]:
    """Per-param rationale, when the model supplied one. Not required — a missing rationale is a
    thin proposal, not an invalid one — but kept when present, because it is what makes the advice
    artifact readable by a human three weeks later."""
    if _is_single_entry(value):
        value = [value]
    if not isinstance(value, list):
        return {}
    return {
        str(item["param"]): str(item.get("rationale") or "")
        for item in value
        if isinstance(item, dict) and "param" in item and item.get("rationale")
    }


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Split a parsed reply into `{observations, flags, proposals, malformed}`.

    `proposals` are readable and typed; `malformed` carry the reason they are not, so the caller can
    record both against the checkpoint.
    """
    observations = [str(o) for o in payload.get("observations") or [] if str(o).strip()]

    flags = []
    for flag in payload.get("flags") or []:
        if isinstance(flag, dict) and flag.get("text"):
            flags.append({
                "module": str(flag.get("module") or "suite"),
                "severity": str(flag.get("severity") or "info"),
                "text": str(flag["text"]),
            })

    proposals: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for raw in payload.get("proposals") or []:
        typed, reason = _typed(raw)
        (proposals if reason is None else malformed).append(
            typed if reason is None else {"raw": raw, "reason": reason}
        )

    return {
        "observations": observations,
        "flags": flags,
        "proposals": proposals,
        "malformed": malformed,
    }


def _typed(raw: Any) -> tuple[dict[str, Any], str | None]:
    if not isinstance(raw, dict):
        return {}, "proposal is not an object"
    kind = raw.get("kind")
    if kind not in KINDS:
        return {}, f"unknown_kind: {kind!r} (expected one of {', '.join(KINDS)})"

    missing = [f for f in _REQUIRED[kind] if not raw.get(f)]
    if missing:
        return {}, f"missing required field(s) for {kind}: {', '.join(missing)}"

    out: dict[str, Any] = {"kind": kind, "module": raw.get("module"), "raw": raw}

    if kind in ("bounded_adjustment", "experiment_spec", "tune"):
        params, reason = _as_params(raw.get("params"))
        if reason:
            return {}, reason
        out["params"] = params
        out["rationales"] = _rationales(raw.get("params"))

    if kind == "experiment_spec":
        out.update({
            "name": str(raw.get("name")),
            "hypothesis": str(raw.get("hypothesis") or ""),
            "success_metric": str(raw.get("success_metric") or ""),
            "sessions": raw.get("sessions"),
        })
    if kind == "bounded_adjustment":
        out.update({"hypothesis": str(raw.get("hypothesis") or ""), "sessions": raw.get("sessions")})
    if kind == "tune":
        out["experiment_id"] = str(raw.get("experiment_id"))
        out["rationale"] = str(raw.get("rationale") or "")
    if kind == "creative":
        out.update({
            "title": str(raw.get("title")),
            "text": str(raw.get("text") or ""),
            # A creative idea is only actionable if it arrives ready to paste. Kept verbatim as a
            # string so nothing here has to understand a module's config shape.
            "spec_json": raw.get("spec_json"),
        })
    if kind == "verdict":
        recommendation = str(raw.get("recommendation") or "")
        if recommendation not in RECOMMENDATIONS:
            return {}, f"recommendation must be one of {', '.join(RECOMMENDATIONS)}"
        out.update({
            "experiment_id": str(raw.get("experiment_id")),
            "recommendation": recommendation,
            "rationale": str(raw.get("rationale") or ""),
        })

    return out, None


def parse(raw: str) -> dict[str, Any]:
    """`locate_json` then `normalize` — the whole read side of a reply in one call."""
    return normalize(locate_json(raw))
