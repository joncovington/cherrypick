r"""Tests for tools/check_guide.py.

Each rule is shown failing: a small temp repo is built that breaks exactly one invariant, and the
test asserts the finding. A clean layout must pass, so a rule that cries wolf is caught too. The last
test holds the real tree to the checker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_guide as cg  # noqa: E402

INDEX = "# The guide\n\n## Group\n\n- [Alpha](alpha.md): the first page.\n"
CLI = 'choices=["status", "doctor"]\nadd_argument("--restart")\n'


def build(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    base = {
        "docs/guide/README.md": INDEX,
        "docs/guide/alpha.md": "# Alpha\n\n## Setup\n\nText.\n",
        cg.cd._CLI_SOURCE: CLI,
        "packages/core/cherrypick/util.py": "",
    }
    base.update(files)
    for rel, body in base.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")
    return tmp_path


def alpha(body: str) -> dict[str, str]:
    return {"docs/guide/alpha.md": "# Alpha\n\n" + body}


def rules(findings: list[str]) -> str:
    return "\n".join(findings)


def test_clean_layout_passes(tmp_path):
    assert cg.check(build(tmp_path, {})) == []


# ---- rule 1
def test_rule1_unlisted_page(tmp_path):
    root = build(tmp_path, {"docs/guide/beta.md": "# Beta\n"})
    assert "beta.md:1: rule 1: page is not listed" in rules(cg.check(root))


def test_rule1_listed_but_missing(tmp_path):
    root = build(tmp_path, {"docs/guide/README.md": INDEX + "- [Gone](gone.md): nope.\n"})
    assert "rule 1: `gone.md` is listed but does not exist" in rules(cg.check(root))


def test_rule1_listed_twice(tmp_path):
    root = build(tmp_path, {"docs/guide/README.md": INDEX + "- [Again](alpha.md): dup.\n"})
    assert "rule 1: `alpha.md` is listed twice" in rules(cg.check(root))


@pytest.mark.parametrize(
    "item",
    [
        "- [Alpha](alpha.md) no colon",
        "- [Alpha](Alpha.md): caps.",
        "- Alpha: bare.",
        "- [Alpha](../alpha.md): relative up.",
    ],
)
def test_rule1_malformed_item(tmp_path, item):
    root = build(tmp_path, {"docs/guide/README.md": INDEX + item + "\n"})
    assert "rule 1: index item is not" in rules(cg.check(root))


def test_rule1_bad_slug(tmp_path):
    root = build(tmp_path, {"docs/guide/Bad_Name.md": "# x\n"})
    assert "rule 1: slug must be" in rules(cg.check(root))


# ---- rule 2
def test_rule2_broken_file_link(tmp_path):
    root = build(tmp_path, alpha("See [x](nope.md).\n"))
    assert "alpha.md:3: rule 2: link target `nope.md`" in rules(cg.check(root))


def test_rule2_broken_anchor_same_page_and_other(tmp_path):
    root = build(tmp_path, alpha("[a](#missing) and [b](README.md#nothing)\n"))
    out = rules(cg.check(root))
    assert "no heading `#missing`" in out and "no heading `#nothing`" in out


def test_rule2_good_anchors_and_dedupe(tmp_path):
    body = "## Setup\n\n[a](#setup) [b](#setup-1) [c](#a-b----c)\n\n## A, B -- C\n\n## Setup\n"
    root = build(tmp_path, alpha(body))
    assert cg.check(root) == []


def test_rule2_links_in_code_are_ignored(tmp_path):
    root = build(tmp_path, alpha("`[x](nope.md)`\n"))
    assert cg.check(root) == []


def test_rule2_slugify_matches_console():
    assert cg.slugify("What's `run.py` doing? (A_b)") == "whats-runpy-doing-a_b"
    assert cg.slugify("A  B") == "a--b"


WEB = "packages/console/web/src/x.tsx"


def test_rule2_console_unknown_slug_and_anchor(tmp_path):
    src = 'a("/help/alpha");\nb("/help/ghost");\nc(`/help/alpha#nope`);\nd("/help/faq");\n'
    root = build(tmp_path, {WEB: src})
    out = rules(cg.check(root))
    assert f"{WEB}:2: rule 2: /help/ghost" in out
    assert f"{WEB}:3: rule 2: no heading `#nope`" in out
    assert f"{WEB}:1" not in out and f"{WEB}:4" not in out


def test_rule2_console_comments_are_not_literals(tmp_path):
    root = build(tmp_path, {WEB: ' * links go to `/help/other#x`\n// "/help/other"\n'})
    assert cg.check(root) == []


# ---- rule 3
def test_rule3_missing_image(tmp_path):
    root = build(tmp_path, alpha("![x](../images/none.png)\n"))
    assert "rule 3: image `../images/none.png` does not exist" in rules(cg.check(root))


def test_rule3_unused_image(tmp_path):
    root = build(tmp_path, {"docs/images/orphan.png": b"x"})
    assert "orphan.png:1: rule 3: image is not referenced" in rules(cg.check(root))


def test_rule3_oversize_image(tmp_path):
    root = build(
        tmp_path,
        {"docs/images/big.png": b"x" * (150 * 1024 + 1), **alpha("![x](../images/big.png)\n")},
    )
    assert "big.png:1: rule 3: image is over 150 KB" in rules(cg.check(root))


def test_rule3_referenced_small_image_passes(tmp_path):
    root = build(
        tmp_path,
        {
            "docs/images/ok.png": b"x" * 150 * 1024,
            "docs/images/shots.json": manifest(manual=[{"file": "ok.png"}]),
            **alpha("![x](../images/ok.png)\n"),
        },
    )
    assert cg.check(root) == []


# ---- rule 4
FAQ_INDEX = INDEX + "- [FAQ](faq.md): answers.\n"
GOOD_FAQ = "# FAQ\n\nIntro.\n\n## Group\n\n### One?\n\nYes.\n\n### Two?\n\nNo.\n"


def faq(body: str) -> dict[str, str]:
    return {"docs/guide/README.md": FAQ_INDEX, "docs/guide/faq.md": body}


def test_rule4_good_faq(tmp_path):
    assert cg.check(build(tmp_path, faq(GOOD_FAQ))) == []


@pytest.mark.parametrize(
    "body,expect",
    [
        ("Intro only.\n", "must start with a `#` title"),
        (GOOD_FAQ.replace("Yes.", ""), "question `One?` has no answer"),
        (GOOD_FAQ + "\n#### Deep\n\nx\n", "deeper than `###`"),
        (GOOD_FAQ.replace("Two?", "One?"), "duplicate question"),
        (GOOD_FAQ + "\n## Empty\n", "group has no `###` question"),
        ("# FAQ\n\n### Orphan?\n\nA.\n", "outside any `##` group"),
    ],
)
def test_rule4_shape(tmp_path, body, expect):
    assert expect in rules(cg.check(build(tmp_path, faq(body))))


# ---- rules 5-7
def test_rule5_unknown_verb(tmp_path):
    root = build(tmp_path, alpha("Run `python run.py frobnicate` now.\n"))
    assert "rule 5: `run.py frobnicate`" in rules(cg.check(root))


def test_rule5_verb_in_fence_and_prose_ignored(tmp_path):
    root = build(tmp_path, alpha("run.py frobnicate in prose.\n\n```\npython run.py status\n```\n"))
    assert cg.check(root) == []
    root2 = build(tmp_path, alpha("```\npython run.py frobnicate\n```\n"))
    assert "rule 5" in rules(cg.check(root2))


def test_rule6_missing_module(tmp_path):
    root = build(tmp_path, alpha("`python -m cherrypick.ghost` and `python -m cherrypick.util`\n"))
    out = rules(cg.check(root))
    assert "rule 6: module `cherrypick.ghost`" in out and "cherrypick.util`" not in out


def test_rule7_unknown_flag(tmp_path):
    root = build(tmp_path, alpha("`python run.py status --bogus --restart`\n"))
    out = rules(cg.check(root))
    assert "rule 7: `--bogus`" in out and "--restart" not in out


def test_rule7_flags_of_other_commands_ignored(tmp_path):
    root = build(tmp_path, alpha("`node ui-check.mjs --route x` and `python run.py status`\n"))
    assert cg.check(root) == []


# ---- rule 8
NAV = (
    'export const SUITE_LINKS = [\n  { to: "/live", label: "Live" },\n  { to: "/help", label: "Help" },\n];\n'
)


def test_rule8_console_page_without_a_guide(tmp_path):
    root = build(tmp_path, {cg.NAV_LINKS: NAV})
    out = rules(cg.check(root))
    assert "rule 8: console page `/live` has no guide page" in out
    assert "`/help`" not in out  # Help is the guide itself


def test_rule8_route_or_tab_in_inline_code_covers_it(tmp_path):
    for body in ("It is at `/live`.\n", "Open `/live/positions`.\n", "See `/live?pf=x`.\n"):
        root = build(tmp_path, {cg.NAV_LINKS: NAV, **alpha(body)})
        assert cg.check(root) == [], body


def test_rule8_prose_or_a_longer_route_does_not_count(tmp_path):
    for body in ("It is at /live.\n", "Open `/livestock`.\n", "Path `docs/live`.\n"):
        root = build(tmp_path, {cg.NAV_LINKS: NAV, **alpha(body)})
        assert "rule 8" in rules(cg.check(root)), body


def test_rule8_an_unlisted_page_does_not_count(tmp_path):
    root = build(tmp_path, {cg.NAV_LINKS: NAV, "docs/guide/beta.md": "# Beta\n\n`/live`\n"})
    assert "rule 8: console page `/live`" in rules(cg.check(root))


# ---- rule 9
ORDER = (
    'export const TRADING_MODULE_ORDER = ["meic"] as const;\n'
    'export const SUITE_ORDER = ["live", "config"] as const;\n'
)
SRC = "packages/console/web/src/pages/Live/LiveTodayPage.tsx"


def manifest(shots: list | None = None, manual: list | None = None) -> str:
    import json

    return json.dumps({"demo": {"seed": 7, "days": 30}, "shots": shots or [], "manual": manual or []})


def shot(**over) -> dict:
    return {"file": "live-today.png", "route": "live/today", "card": None, "sources": [SRC], **over}


def with_shot(tmp_path, entry: dict, extra: dict | None = None) -> Path:
    return build(
        tmp_path,
        {
            cg.MODULE_ORDER: ORDER,
            SRC: "",
            "docs/images/live-today.png": b"png",
            "docs/images/shots.json": manifest([entry]),
            **alpha("![x](../images/live-today.png)\n"),
            **(extra or {}),
        },
    )


def test_rule9_a_good_entry_passes(tmp_path):
    assert cg.check(with_shot(tmp_path, shot())) == []


def test_rule9_image_not_in_the_manifest(tmp_path):
    root = with_shot(
        tmp_path,
        shot(),
        {
            "docs/images/other.png": b"png",
            **alpha("![x](../images/live-today.png) ![y](../images/other.png)\n"),
        },
    )
    assert "docs/images/other.png:1: rule 9: image is not in shots.json" in rules(cg.check(root))


def test_rule9_images_without_any_manifest(tmp_path):
    root = build(tmp_path, {"docs/images/a.png": b"png", **alpha("![x](../images/a.png)\n")})
    assert "rule 9: no manifest" in rules(cg.check(root))


def test_rule9_listed_but_missing_and_listed_twice(tmp_path):
    root = with_shot(
        tmp_path, shot(), {"docs/images/shots.json": manifest([shot(), shot()], [{"file": "gone.png"}])}
    )
    out = rules(cg.check(root))
    assert "`gone.png` is listed but not in docs/images" in out
    assert "`live-today.png` is listed more than once" in out


def test_rule9_route_that_is_not_a_console_page(tmp_path):
    assert "is not a console page" in rules(cg.check(with_shot(tmp_path, shot(route="nowhere/tab"))))


def test_rule9_missing_or_absent_sources(tmp_path):
    assert "names no source files" in rules(cg.check(with_shot(tmp_path, shot(sources=[]))))
    out = rules(cg.check(with_shot(tmp_path, shot(sources=["web/src/pages/Live/LiveTodayPage.tsx"]))))
    assert "source `web/src/pages/Live/LiveTodayPage.tsx` does not exist" in out


def test_rule9_unreadable_manifest(tmp_path):
    root = with_shot(tmp_path, shot(), {"docs/images/shots.json": "{not json"})
    assert "rule 9: unreadable manifest" in rules(cg.check(root))


# ---- the stale-screenshot warning (tools/check_screenshots_fresh.py)
def test_fresh_warns_only_when_a_source_changes_without_the_image():
    import check_screenshots_fresh as fresh

    m = {"shots": [shot()]}
    assert fresh.stale({SRC}, m) == [("live-today.png", [SRC])]
    assert fresh.stale({SRC, "docs/images/live-today.png"}, m) == []
    assert fresh.stale({"README.md"}, m) == []


def test_fresh_prints_a_warning_and_never_fails(tmp_path, capsys):
    import check_screenshots_fresh as fresh

    root = with_shot(tmp_path, shot())
    assert fresh.main(stdin=f"{SRC}\n", root=root) == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning file=docs/images/live-today.png::") and "screenshots-ok" in out


# ---- the real tree
def test_real_repo_passes():
    assert cg.check() == []
