"""Needs-a-look alerts (managealert.py): a name is posted the first time it appears in a session, once
for all its legs, and never again that day; a new session starts empty; nothing new posts nothing."""

import json

from cherrypick.orchestrator import managealert


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, message, embed=None, **kw):
        self.sent.append((level, key, title, message, kw.get("session")))
        return {}


def evaluation(tmp_path, session, items):
    doc = {"session": session, "accounts": [{"attention": {"items": items}}]}
    p = tmp_path / f"eval-{session}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def item(name, leg, level="near", why="2.0% from the strike (alert at 3%)"):
    return {"underlying": name, "leg": leg, "level": level, "why": why}


def test_a_name_posts_once_a_session_with_all_its_legs(tmp_path):
    state, n = tmp_path / "alerts.json", FakeNotifier()
    w = evaluation(
        tmp_path,
        "2026-11-04",
        [
            item("AAA", "2026-12-18 49P"),
            item("AAA", "2026-12-18 51C"),
            item("BBB", "2026-11-20 20P", "in the money", "3.0% through the strike"),
        ],
    )
    assert managealert.run(w, "2026-11-04", state, n) == ["AAA", "BBB"]
    ((level, key, title, message, session),) = n.sent
    assert (
        level == "WARNING"
        and key == "manage.attention"
        and title == "Needs a look: AAA, BBB"
        and session == "2026-11-04"
    )
    assert (
        "AAA 2026-12-18 49P -- near: 2.0% from the strike" in message
        and "BBB 2026-11-20 20P -- in the money" in message
    )
    # The next minute's evaluation, the same names: nothing posts.
    assert managealert.run(w, "2026-11-04", state, n) == [] and len(n.sent) == 1
    # A new name the same day posts on its own.
    w2 = evaluation(tmp_path, "2026-11-04", [item("AAA", "2026-12-18 49P"), item("CCC", "2026-12-18 30P")])
    assert managealert.run(w2, "2026-11-04", state, n) == ["CCC"] and n.sent[-1][2] == "Needs a look: CCC"
    assert json.loads(state.read_text())["names"] == ["AAA", "BBB", "CCC"]


def test_the_next_session_starts_empty(tmp_path):
    state, n = tmp_path / "alerts.json", FakeNotifier()
    managealert.run(evaluation(tmp_path, "2026-11-04", [item("AAA", "x")]), "2026-11-04", state, n)
    assert managealert.run(
        evaluation(tmp_path, "2026-11-05", [item("AAA", "x")]), "2026-11-05", state, n
    ) == ["AAA"]
    assert len(n.sent) == 2


def test_nothing_listed_or_no_evaluation_posts_nothing(tmp_path):
    state, n = tmp_path / "alerts.json", FakeNotifier()
    assert managealert.run(evaluation(tmp_path, "2026-11-04", []), "2026-11-04", state, n) == []
    assert managealert.run(None, "2026-11-04", state, n) == []
    assert managealert.run(str(tmp_path / "missing.json"), "2026-11-04", state, n) == []
    assert n.sent == [] and not state.exists()
