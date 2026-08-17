"""The render prints what the pack says -- dashes for unmeasured, prior labels for prior."""

from datetime import datetime, timezone

from cherrypick.overview import facts, render

SESSION = "2026-08-17"
NOW = datetime(2026, 8, 17, 12, 30, tzinfo=timezone.utc)


def test_render_of_missing_pack_is_none():
    assert render.render(SESSION) is None


def test_empty_pack_renders_dashes_not_zeros():
    facts.write(facts.build(SESSION, now=NOW))
    text = render.render(SESSION)
    assert text is not None
    assert "FRAMEWORK PHASE: YELLOW" in text
    assert "—" in text
    assert "0.00" not in text  # an unmeasured reading must never print as a zero
    assert f"morning-{SESSION}.json" in text  # provenance footer


def test_write_lands_beside_the_pack():
    facts.write(facts.build(SESSION, now=NOW))
    path = render.write(SESSION)
    assert path is not None and path.endswith(f"morning-{SESSION}.md")
