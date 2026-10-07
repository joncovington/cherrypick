"""The outbound record (notify.notifier.send_webhook): every webhook send, kept so it can be checked.

On 2026-10-07 two questions had no answer the suite could give: what the 10-05 QuikOptions post had
actually said, and whether a hand-posted correction had gone out at all. The posts' own logs said
only "posted". These pin what the record must hold for both: the text and images sent, where, the
message id Discord returned, and the files the message was built from, so `run.py sent --stale`
can say a post has gone out of date and `--verify` can say whether it is still there.
"""

from __future__ import annotations

import io
import json
import urllib.error
from argparse import Namespace

import pytest

from cherrypick.notify import notifier, secrets

URL = "https://discord.example/api/webhooks/1/SECRET-TOKEN"


class _Resp:
    def __init__(self, status=200, body=b'{"id": "1234567890"}'):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(seen, resp=None):
    def opener(req, timeout):
        seen.append(req)
        return resp or _Resp()

    return opener


def _records():
    return notifier.read_outbound()


def test_a_send_is_recorded_with_its_text_images_id_and_inputs(tmp_path):
    seen = []
    capture = tmp_path / "2026-10-05.json"
    capture.write_text('{"a": 1}', encoding="utf-8")
    image = tmp_path / "card.png"
    image.write_bytes(b"png")
    out = notifier.send_webhook(
        URL, {"content": "TCP Options Report"}, [image], channel="discord_reporting",
        source="quikoptions-post", kind="report", session="2026-10-05", inputs=[capture],
        opener=_opener(seen),
    )  # fmt: skip
    assert out == {"ok": True, "status": 200, "message_id": "1234567890", "error": None}
    assert seen[0].full_url.endswith("?wait=true")  # Discord returns the message, so its id
    (rec,) = _records()
    assert rec["content"] == "TCP Options Report" and rec["message_id"] == "1234567890"
    assert (rec["channel"], rec["source"], rec["kind"], rec["session"]) == (
        "discord_reporting", "quikoptions-post", "report", "2026-10-05",
    )  # fmt: skip
    assert rec["attachments"][0]["name"] == "card.png" and len(rec["attachments"][0]["sha256"]) == 64
    assert rec["inputs"][0]["path"] == str(capture) and "sha256" in rec["inputs"][0]


def test_the_webhook_url_is_never_recorded(tmp_path):
    notifier.send_webhook(
        URL, {"content": "x"}, channel="discord", source="t", kind="note", opener=_opener([])
    )
    text = (
        (notifier.outbound_dir())
        .joinpath(next(notifier.outbound_dir().glob("*.jsonl")).name)
        .read_text("utf-8")
    )
    assert "SECRET-TOKEN" not in text and "discord.example" not in text


def test_a_failed_send_is_recorded_as_failed():
    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"{}"))

    out = notifier.send_webhook(
        URL, {"content": "x"}, channel="discord", source="t", kind="note", opener=refuse
    )
    assert out["ok"] is False and out["error"] == "HTTP 400"
    (rec,) = _records()
    assert rec["ok"] is False and rec["status"] == 400 and rec["message_id"] is None


def test_slack_gets_no_wait_and_records_its_text():
    seen = []
    notifier.send_webhook(
        "https://hooks.slack.example/x", {"text": "hello"}, channel="slack", source="t", kind="notify",
        opener=_opener(seen, _Resp(200, b"ok")),
    )  # fmt: skip
    assert "wait=true" not in seen[0].full_url
    (rec,) = _records()
    assert rec["content"] == "hello" and rec["message_id"] is None


def test_a_record_that_cannot_be_written_never_costs_the_send(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    monkeypatch.setattr(notifier, "outbound_dir", lambda: blocked)
    out = notifier.send_webhook(
        URL, {"content": "x"}, channel="discord", source="t", kind="note", opener=_opener([])
    )
    assert out["ok"] is True
    # And on its own: record_outbound promises never to raise, whoever calls it.
    notifier.record_outbound({"ts": "2026-10-07T00:00:00+00:00", "content": "x"})


def test_changed_inputs_names_what_moved_after_the_post(tmp_path):
    kept, edited, gone = (tmp_path / n for n in ("kept.json", "edited.json", "gone.json"))
    for p in (kept, edited, gone):
        p.write_text("{}", encoding="utf-8")
    notifier.send_webhook(
        URL, {"content": "x"}, channel="discord", source="t", kind="report", inputs=[kept, edited, gone],
        opener=_opener([]),
    )  # fmt: skip
    (rec,) = _records()
    assert notifier.changed_inputs(rec) == []
    edited.write_text('{"direction": "bought"}', encoding="utf-8")
    gone.unlink()
    changed = {c["path"]: c["now"] for c in notifier.changed_inputs(rec)}
    assert set(changed) == {str(edited), str(gone)}
    assert changed[str(gone)].get("missing") is True


def test_verify_asks_discord_for_the_message(monkeypatch):
    monkeypatch.setattr(secrets, "read_entry", lambda name: URL if name == "discord" else None)
    entry = {"channel": "discord", "message_id": "42"}
    seen = []
    assert notifier.verify_message(entry, opener=_opener(seen)) == "present"
    assert seen[0].full_url == URL + "/messages/42"

    def missing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, io.BytesIO(b"{}"))

    assert notifier.verify_message(entry, opener=missing) == "deleted"
    assert notifier.verify_message({"channel": "discord"}).startswith("unknown")
    assert notifier.verify_message({"channel": "slack", "message_id": "1"}).startswith("unknown")


def test_a_notifier_push_is_recorded_under_its_key(monkeypatch):
    monkeypatch.setattr(secrets, "get_webhook", lambda ch: URL if ch == "discord" else None)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", lambda req, timeout: _Resp())
    notifier.Notifier({"channels": ["discord"]}).notify(
        "INFO", "status.digest", "T", "B", kind="digest", session="2026-10-07"
    )
    (rec,) = _records()
    assert (rec["source"], rec["kind"], rec["session"], rec["message_id"]) == (
        "status.digest", "digest", "2026-10-07", "1234567890",
    )  # fmt: skip


# --------------------------------------------------------------------------- the commands


def _args(**kw):
    base = dict(
        date=None, days=7, kind=None, stale=False, verify=False, json=False,
        channel=None, file=None, image=None, refers_to=None, dry_run=False,
    )  # fmt: skip
    return Namespace(**{**base, **kw})


def test_notify_send_reads_its_text_from_a_file_and_links_the_correction(tmp_path, monkeypatch, capsys):
    from cherrypick import cli

    monkeypatch.setattr(secrets, "read_entry", lambda name: URL)
    monkeypatch.setattr(notifier.urllib.request, "urlopen", lambda req, timeout: _Resp())
    original = notifier.send_webhook(
        URL, {"content": "report"}, channel="discord_reporting", source="quikoptions-post", kind="report",
        session="2026-10-05", opener=lambda req, timeout: _Resp(200, b'{"id": "111"}'),
    )  # fmt: skip
    text = tmp_path / "correction.md"
    text.write_text("NVDA 220/170P ($17.55M): bought, bearish", encoding="utf-8")  # the $1 survives
    cli.cmd_notify_send(
        _args(channel="discord_reporting", file=str(text), kind="correction", date="2026-10-05",
              refers_to=original["message_id"])
    )  # fmt: skip
    first, second = _records()
    assert second["content"] == "NVDA 220/170P ($17.55M): bought, bearish"
    assert (second["kind"], second["refers_to"], second["source"]) == ("correction", "111", "notify-send")
    capsys.readouterr()
    cli.cmd_sent(_args(date="2026-10-05"))
    out = capsys.readouterr().out
    assert "followed up" in out and "follows up 111" in out


def test_notify_send_refuses_without_a_file(monkeypatch):
    from cherrypick import cli

    with pytest.raises(SystemExit):
        cli.cmd_notify_send(_args(channel="discord"))
    assert _records() == []


def test_sent_stale_names_the_changed_input(tmp_path, capsys):
    from cherrypick import cli

    capture = tmp_path / "2026-10-05.json"
    capture.write_text("{}", encoding="utf-8")
    notifier.send_webhook(
        URL, {"content": "report"}, channel="discord", source="quikoptions-post", kind="report",
        session="2026-10-05", inputs=[capture], opener=_opener([]),
    )  # fmt: skip
    capture.write_text('{"reparsed": true}', encoding="utf-8")
    cli.cmd_sent(_args(date="2026-10-05", stale=True))
    assert f"STALE: {capture}" in capsys.readouterr().out
    cli.cmd_sent(_args(date="2026-10-05", json=True))
    assert json.loads(capsys.readouterr().out)[0]["session"] == "2026-10-05"
