"""Tests for the Lossdog VIP trade-feed notifier.

Four properties carry the design and are asserted here rather than left to prose:
  - it is off the reliability path (an outage — feed or Clerk — is a skip, never an exception),
  - it seeds instead of backfilling, and never re-notifies an id it has already pushed,
  - the newest trades live on the LAST page, and the backwards walk stops at the first fully-seen
    page instead of re-reading the whole feed every cycle,
  - a dead credential warns exactly once and then stays silent until the credential changes.

Every test stubs the HTTP layer (and the registry/keyring reads); nothing here touches the network.
"""

import base64
import json
import time
import urllib.error

import pytest

import cherrypick.orchestrator.lossdog_notifier as ld
from cherrypick.notify import secrets as notify_secrets
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _jwt(hours=24.0, jti="jti-live") -> str:
    return f"{_b64({'alg': 'none'})}.{_b64({'exp': time.time() + hours * 3600, 'jti': jti})}.sig"


def _trade(i, **over):
    t = {
        "id": f"trade_{i:04d}",
        "underlyingSymbol": "TSLA",
        "underlyingName": "Tesla, Inc.",
        "assetType": "Options",
        "strategyName": "Long Call",
        "strategySlug": "long_call",
        "price": 3.26,
        "priceLabel": "debit",
        "executionTime": f"2026-08-03T{10 + i // 60:02d}:{i % 60:02d}:08Z",  # monotonic with i
        "syncedAt": f"2026-08-03T{11 + i // 60:02d}:{i % 60:02d}:00Z",
        "recordType": "OPTION_STRATEGY",
        "instrumentType": "OPTION",
        "legs": [
            {
                "unitQuantity": 1,
                "expirationDate": "2026-08-21",
                "dte": 18,
                "strike": 360,
                "callOrPut": "CALL",
                "optionType": "CALL",
                "action": "BUY_TO_OPEN",
                "side": "long",
                "averageFillPrice": 3.26,
            }
        ],
        "trader": {
            "userId": "u-1",
            "name": "Tony Battista",
            "profilePictureUrl": "https://api.app.lossdog.com/images/profile-picture/x",
            "jobPosition": "Veteran Trader",
        },
    }
    t.update(over)
    return t


def _fields(embed):
    return {f["name"]: f["value"] for f in embed["fields"]}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point state + lock at a temp dir, capture pushes, and pin the credential surface: no keyring
    cookie, no registry token, a fresh env JWT — individual tests re-wire what they exercise."""
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ld, "_STATE", tmp_path / "lossdog_notify.json")
    monkeypatch.setattr(ld, "_LOCK", tmp_path / "lossdog_notify.lock")
    monkeypatch.setattr(cfgmod, "ensure_dirs", lambda: None)

    sent = []
    monkeypatch.setattr(
        ld.Notifier,
        "notify",
        lambda self, level, key, title, message, embed=None: (
            sent.append((level, key, message, embed)) or {"log": {"ok": True}}
        ),
    )
    monkeypatch.setattr(ld.notify_secrets, "get_lossdog_client", lambda: None)
    monkeypatch.setattr(ld, "_registry_token", lambda: None)
    monkeypatch.setenv("LOSSDOG_TOKEN", _jwt())
    return sent


def _cfg(**over):
    block = {"enabled": True, "channels": ["log"], "max_per_run": 8, "filters": {}}
    block.update(over)
    return {"lossdog": block, "notify": {}}


def _serve(monkeypatch, trades, calls=None):
    """Fake the trades API from a list sorted ascending by executionTime, exactly as the feed is."""

    def fake(token, page, limit):
        if calls is not None:
            calls.append((page, limit))
        return {
            "page": page,
            "limit": limit,
            "items": trades[(page - 1) * limit : page * limit],
            "totalItems": len(trades),
        }

    monkeypatch.setattr(ld, "_get_page", fake)


def _pushed(sent):
    return [key for _level, key, _msg, _embed in sent if key.startswith("lossdog.trade.")]


def _warnings(sent, key):
    return [k for level, k, _msg, _embed in sent if level == "WARNING" and k == key]


# --------------------------------------------------------------------------- reliability
def test_feed_outage_is_a_skip_not_an_exception(wired, monkeypatch):
    monkeypatch.setattr(ld, "_get_page", lambda *_a: None)
    res = ld.run(_cfg())
    assert res["ok"] is True and res["trades_seen"] == 0
    assert wired == []


def test_mid_walk_outage_aborts_the_cycle_with_state_untouched(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1), _trade(2)])
    ld.run(_cfg())  # seed

    def flaky(token, page, limit):
        if limit == 1:
            return {"items": [], "totalItems": 3}  # the probe says something is new...
        return None  # ...but the full page fetch dies mid-walk

    monkeypatch.setattr(ld, "_get_page", flaky)
    res = ld.run(_cfg())
    assert res["ok"] is True and res["notified"] == 0
    assert len(json.loads(ld._STATE.read_text())["notified_ids"]) == 2  # nothing watermarked


def test_disabled_by_default(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1)])
    assert ld.run({"lossdog": {"enabled": False}})["skipped"] == "lossdog not enabled"
    assert ld.run({})["skipped"] == "lossdog not enabled"
    assert wired == []


def test_lock_blocks_a_concurrent_run(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1)])
    ld._LOCK.write_text("123", encoding="utf-8")
    assert "lock" in ld.run(_cfg())["skipped"]
    assert wired == []


def test_get_page_maps_401_to_auth_failed_and_everything_else_to_none(monkeypatch):
    def raiser(code):
        def boom(*_a, **_k):
            raise urllib.error.HTTPError("u", code, "x", None, None)

        return boom

    monkeypatch.setattr(ld.urllib.request, "urlopen", raiser(401))
    assert ld._get_page("t", 1, 1) is ld.AUTH_FAILED
    # 403 is Cloudflare-shaped, 5xx is an outage — neither may masquerade as a dead token.
    for code in (400, 403, 500, 503):
        monkeypatch.setattr(ld.urllib.request, "urlopen", raiser(code))
        assert ld._get_page("t", 1, 1) is None

    def boom(*_a, **_k):
        raise OSError("connection reset")

    monkeypatch.setattr(ld.urllib.request, "urlopen", boom)
    assert ld._get_page("t", 1, 1) is None


# --------------------------------------------------------------------------- watermark
def test_first_run_seeds_without_backfilling(wired, monkeypatch):
    _serve(monkeypatch, [_trade(i) for i in range(1, 21)])
    res = ld.run(_cfg())
    assert res["seeded"] is True
    assert wired == []  # switching it on must not blast the existing feed
    ids = json.loads(ld._STATE.read_text())["notified_ids"]
    assert set(ids) == {f"trade_{i:04d}" for i in range(1, 21)}


def test_only_new_trades_notify_and_never_twice(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1), _trade(2)])
    ld.run(_cfg())  # seed

    _serve(monkeypatch, [_trade(1), _trade(2), _trade(3)])
    assert ld.run(_cfg())["notified"] == 1
    assert _pushed(wired) == ["lossdog.trade.trade_0003"]

    assert ld.run(_cfg())["notified"] == 0  # same window again — no re-notify
    assert len(_pushed(wired)) == 1


def test_unchanged_total_skips_the_page_walk(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1), _trade(2)])
    ld.run(_cfg())  # seed

    calls = []
    _serve(monkeypatch, [_trade(1), _trade(2)], calls)
    ld.run(_cfg())
    assert calls == [(1, 1)]  # the cheap probe only — the common case at a 10-minute cadence


def test_backwards_walk_stops_at_the_first_fully_seen_page(wired, monkeypatch):
    _serve(monkeypatch, [_trade(i) for i in range(250)])
    ld.run(_cfg())  # seed 250 ids (3 pages at limit=100)

    calls = []
    _serve(monkeypatch, [_trade(i) for i in range(255)], calls)  # 5 new trades appended
    res = ld.run(_cfg())
    assert res["notified"] == 5
    # Probe, then the LAST page (has the new trades), then one fully-seen page — and stop.
    assert calls == [(1, 1), (3, 100), (2, 100)]


def test_burst_is_capped_and_remainder_watermarked(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())  # seed

    _serve(monkeypatch, [_trade(i) for i in range(1, 32)])
    res = ld.run(_cfg(max_per_run=3))
    assert res["notified"] == 3 and res["suppressed"] == 27
    assert _pushed(wired) == [
        "lossdog.trade.trade_0029",
        "lossdog.trade.trade_0030",
        "lossdog.trade.trade_0031",
    ]
    # Suppressed ids are watermarked, not left to resurface on the next tick.
    _serve(monkeypatch, [_trade(i) for i in range(1, 32)])
    assert ld.run(_cfg(max_per_run=3))["notified"] == 0


def test_trades_push_in_chronological_order(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())  # seed
    _serve(monkeypatch, [_trade(1), _trade(4), _trade(2), _trade(3)])
    ld.run(_cfg())
    assert _pushed(wired) == [
        "lossdog.trade.trade_0002",
        "lossdog.trade.trade_0003",
        "lossdog.trade.trade_0004",
    ]


def test_id_cap_bounds_the_state_file(wired, monkeypatch):
    monkeypatch.setattr(ld, "_ID_CAP", 10)
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())  # seed
    _serve(monkeypatch, [_trade(i) for i in range(1, 31)])
    ld.run(_cfg())
    ids = json.loads(ld._STATE.read_text())["notified_ids"]
    assert len(ids) == 10
    assert ids[-1] == "trade_0030"  # the cap keeps the newest, insertion-ordered


def test_a_trade_without_an_id_is_skipped_and_not_watermarked(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())  # seed
    _serve(monkeypatch, [_trade(1), {"underlyingSymbol": "??"}, _trade(2)])
    res = ld.run(_cfg())
    assert res["ok"] is True and res["notified"] == 1  # the mangled row cannot kill the cycle
    assert "??" not in json.dumps(json.loads(ld._STATE.read_text())["notified_ids"])


def test_filtered_out_trade_is_watermarked_silently(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())  # seed
    _serve(monkeypatch, [_trade(1), _trade(2, underlyingSymbol="IWM"), _trade(3)])
    res = ld.run(_cfg(filters={"underlying_symbols": ["TSLA"]}))
    assert res["notified"] == 1 and res["filtered"] == 1
    assert _pushed(wired) == ["lossdog.trade.trade_0003"]
    # The filtered trade never resurfaces, even if the filter is later removed.
    _serve(monkeypatch, [_trade(1), _trade(2, underlyingSymbol="IWM"), _trade(3)])
    assert ld.run(_cfg())["notified"] == 0


# --------------------------------------------------------------------------- replay / dry-run
def test_dry_run_posts_nothing_and_leaves_state_untouched(wired, monkeypatch, capsys):
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())  # seed
    before = ld._STATE.read_text()
    _serve(monkeypatch, [_trade(1), _trade(2)])
    res = ld.run(_cfg(), dry_run=True)
    assert res["dry_run"] is True and res["would_notify"] == 1
    assert _pushed(wired) == []
    assert json.loads(capsys.readouterr().out.strip())["embed"]["title"] == "OPEN · TSLA Long Call"
    assert ld._STATE.read_text() == before


def test_replay_last_reposts_regardless_of_seen_state_and_the_enabled_gate(wired, monkeypatch):
    _serve(monkeypatch, [_trade(i) for i in range(1, 6)])
    ld.run(_cfg())  # seed everything
    before = ld._STATE.read_text()
    res = ld.run(_cfg(enabled=False), replay_last=2)  # disabled config: the gate is bypassed
    assert res["replayed"] == 2
    assert _pushed(wired) == ["lossdog.trade.trade_0004", "lossdog.trade.trade_0005"]
    assert ld._STATE.read_text() == before


def test_dry_run_before_first_seed_reports_and_writes_nothing(wired, monkeypatch):
    _serve(monkeypatch, [_trade(1), _trade(2)])
    res = ld.run(_cfg(), dry_run=True)
    assert res["would_seed"] is True
    assert not ld._STATE.exists()


# --------------------------------------------------------------------------- credentials
def test_minted_token_is_preferred_and_used_as_the_bearer(wired, monkeypatch):
    minted = _jwt(jti="minted")
    monkeypatch.setattr(ld.notify_secrets, "get_lossdog_client", lambda: "client-cookie")
    monkeypatch.setattr(ld, "_mint_token", lambda cookie: minted)
    used = []

    def fake(token, page, limit):
        used.append(token)
        return {"items": [_trade(1)], "totalItems": 1}

    monkeypatch.setattr(ld, "_get_page", fake)
    res = ld.run(_cfg())
    assert res["token_source"] == "minted"
    assert set(used) == {minted}  # never the env token, never the cookie itself


def test_mint_token_handles_both_clerk_response_shapes(monkeypatch):
    responses = {
        ("GET", "/v1/client"): {"response": {"last_active_session_id": "sess_1"}},
        ("POST", "/v1/client/sessions/sess_1/tokens/canis-amnis"): {"jwt": "tok"},
    }
    monkeypatch.setattr(ld, "_clerk_json", lambda cookie, path, method="GET": responses[(method, path)])
    assert ld._mint_token("c") == "tok"
    responses[("GET", "/v1/client")] = {"sessions": [{"id": "sess_1"}]}
    responses[("POST", "/v1/client/sessions/sess_1/tokens/canis-amnis")] = {"response": {"jwt": "tok2"}}
    assert ld._mint_token("c") == "tok2"


def test_clerk_outage_falls_back_to_the_env_token_silently(wired, monkeypatch):
    monkeypatch.setattr(ld.notify_secrets, "get_lossdog_client", lambda: "client-cookie")
    monkeypatch.setattr(ld, "_mint_token", lambda cookie: None)  # unreachable / shape drift
    _serve(monkeypatch, [_trade(1)])
    res = ld.run(_cfg())
    assert res["token_source"] == "env" and res["seeded"] is True
    assert [s for s in wired if s[0] == "WARNING"] == []  # not the cookie's fault — no warning


def test_dead_cookie_warns_once_while_the_env_token_carries_the_feed(wired, monkeypatch):
    monkeypatch.setattr(ld.notify_secrets, "get_lossdog_client", lambda: "client-cookie")
    monkeypatch.setattr(ld, "_mint_token", lambda cookie: ld.AUTH_FAILED)
    _serve(monkeypatch, [_trade(1)])
    assert ld.run(_cfg())["token_source"] == "env"
    assert len(_warnings(wired, "lossdog.cookie")) == 1
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())
    assert len(_warnings(wired, "lossdog.cookie")) == 1  # once per cookie, not per tick


def test_missing_credential_warns_once_and_skips(wired, monkeypatch):
    monkeypatch.delenv("LOSSDOG_TOKEN")
    res = ld.run(_cfg())
    assert res["skipped"] == "no credential" and res["token_source"] == "missing"
    assert len(_warnings(wired, "lossdog.auth")) == 1
    ld.run(_cfg())
    assert len(_warnings(wired, "lossdog.auth")) == 1


def test_unreadable_keyring_falls_back_to_the_env_token_silently(wired, monkeypatch):
    monkeypatch.setattr(
        ld.notify_secrets, "get_lossdog_client", lambda: ld.notify_secrets.KEYRING_UNAVAILABLE
    )
    _serve(monkeypatch, [_trade(1)])
    res = ld.run(_cfg())
    assert res["token_source"] == "env" and res["seeded"] is True
    assert [s for s in wired if s[0] == "WARNING"] == []


def test_unreadable_keyring_stays_quiet_for_a_hiccup_then_warns_on_an_outage(wired, monkeypatch):
    monkeypatch.delenv("LOSSDOG_TOKEN")
    monkeypatch.setattr(
        ld.notify_secrets, "get_lossdog_client", lambda: ld.notify_secrets.KEYRING_UNAVAILABLE
    )
    for run_no in range(1, ld._KEYRING_OUTAGE_RUNS):
        res = ld.run(_cfg())
        assert res["skipped"] == "keyring unavailable" and res["keyring_unavailable_runs"] == run_no
        assert _warnings(wired, "lossdog.auth") == []  # a Credential Manager hiccup is not an alarm
    res = ld.run(_cfg())
    assert res["skipped"] == "no credential" and res["token_source"] == "keyring_unavailable"
    assert len(_warnings(wired, "lossdog.auth")) == 1
    ld.run(_cfg())
    assert len(_warnings(wired, "lossdog.auth")) == 1  # once per outage, not per tick


def test_a_keyring_that_answers_again_re_arms_the_outage_count(wired, monkeypatch):
    monkeypatch.delenv("LOSSDOG_TOKEN")
    monkeypatch.setattr(
        ld.notify_secrets, "get_lossdog_client", lambda: ld.notify_secrets.KEYRING_UNAVAILABLE
    )
    assert ld.run(_cfg())["keyring_unavailable_runs"] == 1
    monkeypatch.setattr(ld.notify_secrets, "get_lossdog_client", lambda: "client-cookie")
    monkeypatch.setattr(ld, "_mint_token", lambda cookie: _jwt(jti="minted"))
    _serve(monkeypatch, [_trade(1)])
    assert ld.run(_cfg())["token_source"] == "minted"
    monkeypatch.setattr(
        ld.notify_secrets, "get_lossdog_client", lambda: ld.notify_secrets.KEYRING_UNAVAILABLE
    )
    assert ld.run(_cfg())["keyring_unavailable_runs"] == 1  # counted from the new outage, not the old


def test_401_warns_once_then_stays_silent_until_the_token_changes(wired, monkeypatch):
    monkeypatch.setattr(ld, "_get_page", lambda *_a: ld.AUTH_FAILED)
    assert ld.run(_cfg())["skipped"] == "auth failed (401)"
    ld.run(_cfg())
    assert len(_warnings(wired, "lossdog.auth")) == 1
    monkeypatch.setenv("LOSSDOG_TOKEN", _jwt(jti="rotated"))  # a new credential re-arms the warning
    ld.run(_cfg())
    assert len(_warnings(wired, "lossdog.auth")) == 2


def test_401_retries_once_with_a_differing_registry_token(wired, monkeypatch):
    stale, fresh = _jwt(jti="stale"), _jwt(jti="fresh")
    monkeypatch.setenv("LOSSDOG_TOKEN", stale)
    monkeypatch.setattr(ld, "_registry_token", lambda: fresh)

    def fake(token, page, limit):
        if token == stale:
            return ld.AUTH_FAILED  # the daemon's env holds yesterday's token...
        return {"items": [_trade(1)], "totalItems": 1}  # ...the registry has today's

    monkeypatch.setattr(ld, "_get_page", fake)
    assert ld.run(_cfg())["seeded"] is True
    assert _warnings(wired, "lossdog.auth") == []


def test_a_successful_fetch_rearms_the_auth_warning(wired, monkeypatch):
    monkeypatch.setattr(ld, "_get_page", lambda *_a: ld.AUTH_FAILED)
    ld.run(_cfg())
    assert json.loads(ld._STATE.read_text())["auth_warned_fingerprint"]
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())
    assert not json.loads(ld._STATE.read_text())["auth_warned_fingerprint"]


def test_env_token_expiring_soon_warns_once_per_token(wired, monkeypatch):
    monkeypatch.setenv("LOSSDOG_TOKEN", _jwt(hours=1.0, jti="dying"))
    _serve(monkeypatch, [_trade(1)])
    res = ld.run(_cfg())
    assert res["token_hours_left"] < ld._EXPIRY_WARN_HOURS
    assert len(_warnings(wired, "lossdog.token.expiring")) == 1
    _serve(monkeypatch, [_trade(1)])
    ld.run(_cfg())
    assert len(_warnings(wired, "lossdog.token.expiring")) == 1


def test_a_garbage_token_payload_does_not_crash_the_cycle(wired, monkeypatch):
    monkeypatch.setenv("LOSSDOG_TOKEN", "not-a-jwt-at-all")
    _serve(monkeypatch, [_trade(1)])
    res = ld.run(_cfg())
    assert res["seeded"] is True and "token_hours_left" not in res


def test_an_unreachable_keyring_reads_as_unavailable_not_unset(monkeypatch):
    import keyring.errors

    def boom(service, name):
        raise keyring.errors.KeyringError("Credential Manager is having a moment")

    monkeypatch.setattr(notify_secrets.keyring, "get_password", boom)
    assert notify_secrets.read_entry("lossdog") is notify_secrets.KEYRING_UNAVAILABLE
    assert notify_secrets.get_lossdog_client() is notify_secrets.KEYRING_UNAVAILABLE
    assert notify_secrets.get_webhook("discord") is None  # the "can I post" contract is unchanged
    assert notify_secrets.is_set("discord") is False


def test_secrets_entry_names_are_stable_and_lossdog_is_not_a_webhook():
    assert notify_secrets._entry("discord") == "discord_webhook"
    assert notify_secrets._entry("discord_follow") == "discord_follow_webhook"
    assert notify_secrets._entry("slack") == "slack_webhook"
    assert notify_secrets._entry("lossdog") == "lossdog_client"
    assert "lossdog" in notify_secrets.SUPPORTED


# --------------------------------------------------------------------------- filters
def test_trader_filter_matches_display_names_case_insensitively():
    assert ld._matches_filters(_trade(1), {"traders": ["tony battista"]})
    assert not ld._matches_filters(_trade(1), {"traders": ["Someone Else"]})


def test_symbol_filter():
    assert ld._matches_filters(_trade(1), {"underlying_symbols": ["tsla"]})
    assert not ld._matches_filters(_trade(1), {"underlying_symbols": ["IWM"]})


def test_strategy_filter_matches_slug_or_name():
    assert ld._matches_filters(_trade(1), {"strategy": "long_call"})
    assert ld._matches_filters(_trade(1), {"strategy": "Long Call"})
    assert not ld._matches_filters(_trade(1), {"strategy": "short_strangle"})


def test_open_close_filter_derives_from_leg_actions():
    assert ld._matches_filters(_trade(1), {"open_close": "O"})
    assert not ld._matches_filters(_trade(1), {"open_close": "C"})
    roll = _trade(
        1,
        legs=[
            {"action": "SELL_TO_CLOSE", "strike": 100, "callOrPut": "PUT"},
            {"action": "SELL_TO_OPEN", "strike": 90, "callOrPut": "PUT"},
        ],
    )
    assert not ld._matches_filters(roll, {"open_close": "O"})
    assert not ld._matches_filters(roll, {"open_close": "C"})


# --------------------------------------------------------------------------- formatting
def test_option_leg_line_reads_like_a_trader_says_it():
    leg = _trade(1)["legs"][0]
    assert ld._leg_line(leg) == "BTO 1× 21 Aug 26 $360 CALL @ $3.26 · 18 DTE"


def test_stock_leg_shows_shares_and_fill_only():
    leg = {"unitQuantity": 100, "action": "BUY_TO_OPEN", "averageFillPrice": 174.23}
    assert ld._leg_line(leg) == "BTO 100 sh @ $174.23"


def test_actions_humanize_and_unknown_ones_render_as_words():
    assert [ld._action_abbrev(a) for a in ld._ACTIONS] == ["BTO", "STO", "BTC", "STC"]
    assert ld._action_abbrev("EXERCISE") == "Exercise"


def test_embed_carries_the_whole_trade(wired):
    trade = _trade(7, priceLabel="credit", price=1.95)
    embed = ld.build_embed(trade)
    assert embed["title"] == "OPEN · TSLA Long Call"
    assert embed["color"] == ld.COLOR_CREDIT
    assert embed["author"]["name"] == "Tony Battista · Veteran Trader"
    assert embed["author"]["icon_url"].startswith("https://")
    assert embed["url"] == ld._FEED_URL
    assert embed["description"] == "BTO 1× 21 Aug 26 $360 CALL @ $3.26 · 18 DTE"
    # The follow-feed card's shape: three inline fields, one horizontal strip.
    fields = _fields(embed)
    assert [f["name"] for f in embed["fields"]] == ["Trade", "Context", "Stats"]
    assert all(f["inline"] for f in embed["fields"])
    assert fields["Trade"] == "1× +360C · $1.95 cr"
    assert fields["Context"] == "21 Aug 26 · 18 DTE"
    assert fields["Stats"] == "Options · 1 leg"
    assert embed["timestamp"].endswith("Z")
    assert embed["footer"]["text"] == "Lossdog VIP Trade Feed"


def test_embed_color_is_debit_red_by_default():
    assert ld.build_embed(_trade(1))["color"] == ld.COLOR_DEBIT


def test_author_without_a_picture_omits_the_icon_key():
    trade = _trade(1, trader={"name": "Tony Battista", "jobPosition": ""})
    embed = ld.build_embed(trade)
    assert embed["author"] == {"name": "Tony Battista"}


def test_late_sync_lands_in_the_footer(wired):
    trade = _trade(1, executionTime="2026-08-03T19:07:08Z", syncedAt="2026-08-07T20:00:05Z")
    embed = ld.build_embed(trade)
    assert embed["footer"]["text"] == "Lossdog VIP Trade Feed · synced 4d after execution"
    assert "synced 4d after execution" in ld.format_trade(trade)


def test_unknown_strategy_slug_falls_back_to_the_name_then_the_slug():
    named = _trade(1, strategySlug="weird_new_thing")
    assert ld.build_embed(named)["title"] == "OPEN · TSLA Long Call"
    slug_only = _trade(1, strategyName=None, strategySlug="call_diagonal_spread")
    assert ld.build_embed(slug_only)["title"] == "OPEN · TSLA Call Diagonal Spread"


def test_calendar_expiries_render_as_a_range():
    trade = _trade(
        1,
        legs=[
            {"action": "SELL_TO_OPEN", "strike": 215, "callOrPut": "PUT", "expirationDate": "2026-08-21"},
            {"action": "BUY_TO_OPEN", "strike": 215, "callOrPut": "PUT", "expirationDate": "2026-10-16"},
        ],
    )
    assert _fields(ld.build_embed(trade))["Context"] == "21 Aug 26 – 16 Oct 26"


def test_embed_survives_a_trade_with_almost_nothing_in_it():
    embed = ld.build_embed({"id": "trade_x"})
    assert embed["title"] == "? Trade"  # no legs, so no lifecycle word to lead with
    assert "description" not in embed and "timestamp" not in embed
    assert embed["fields"] == []
    assert ld.format_trade({"id": "trade_x"})  # and the text fallback renders too


def test_plain_text_fallback_carries_head_numbers_and_legs():
    text = ld.format_trade(_trade(1))
    head, numbers, leg = text.split("\n")
    assert head == "➕ Tony Battista · OPEN TSLA Long Call"
    assert numbers == "1× +360C · exp 21 Aug 26 · 18 DTE · $3.26 db · Options · 1 leg · 10:01 UTC"
    assert leg == "> BTO 1× 21 Aug 26 $360 CALL @ $3.26 · 18 DTE"


# --------------------------------------------------------------------------- follow-card parity
def test_compact_structure_signs_every_leg():
    trade = _trade(
        1,
        legs=[
            {"unitQuantity": 1, "action": "SELL_TO_OPEN", "strike": 82, "callOrPut": "PUT"},
            {"unitQuantity": 1, "action": "BUY_TO_OPEN", "strike": 89, "callOrPut": "PUT"},
        ],
    )
    # The sign is the whole message: -82P/+89P is a credit spread, +82P/-89P a debit one.
    assert ld._structure(trade) == "1× -82P/+89P"


def test_three_plus_legs_size_by_gcd_not_by_the_body_leg():
    fly = _trade(
        1,
        legs=[
            {"unitQuantity": 5, "action": "BUY_TO_OPEN", "strike": 457.5, "callOrPut": "PUT"},
            {"unitQuantity": 10, "action": "SELL_TO_OPEN", "strike": 485, "callOrPut": "PUT"},
            {"unitQuantity": 5, "action": "BUY_TO_OPEN", "strike": 512.5, "callOrPut": "PUT"},
        ],
    )
    assert ld._structure(fly) == "5× +457.5P/-485P/+512.5P"


def test_stock_counts_in_shares_and_keeps_its_side():
    trade = _trade(1, assetType="Equity", legs=[{"unitQuantity": 100, "action": "BUY_TO_OPEN"}])
    # "100×" would read as a 100-lot, and the leg body would repeat the underlying.
    assert ld._structure(trade) == "+100 sh"


def test_lifecycle_words_match_the_follow_card():
    assert ld._lifecycle(_trade(1))[1] == "OPEN"
    closing = _trade(1, legs=[{"action": "SELL_TO_CLOSE", "strike": 360, "callOrPut": "CALL"}])
    assert ld._lifecycle(closing)[1] == "CLOSE"
    roll = _trade(
        1,
        legs=[
            {"action": "BUY_TO_CLOSE", "strike": 360, "callOrPut": "CALL"},
            {"action": "SELL_TO_OPEN", "strike": 370, "callOrPut": "CALL"},
        ],
    )
    assert ld._lifecycle(roll)[1] == "ROLL"


def test_money_shows_cents_but_strikes_do_not():
    # $1.5 read as a typo on a live card; $61.00 on a strike would be noise.
    assert ld._price_line(_trade(1, price=1.5, priceLabel="credit")) == "$1.50 cr"
    leg = {
        "unitQuantity": 1,
        "action": "SELL_TO_OPEN",
        "strike": 61,
        "callOrPut": "CALL",
        "averageFillPrice": 1.5,
        "expirationDate": "2026-09-18",
        "dte": 31,
    }
    assert ld._leg_line(leg) == "STO 1× 18 Sep 26 $61 CALL @ $1.50 · 31 DTE"
    # A sub-cent average fill across partials keeps its own precision instead of rounding to $0.06.
    assert ld._money(0.055) == "0.055"
    assert ld._leg_line({"unitQuantity": 100, "action": "BUY_TO_OPEN", "averageFillPrice": 174.2}) == (
        "BTO 100 sh @ $174.20"
    )


def test_price_uses_the_db_cr_abbreviation():
    assert ld._price_line(_trade(1)) == "$3.26 db"
    assert ld._price_line(_trade(1, priceLabel="credit")) == "$3.26 cr"
    # An unknown label is passed through rather than dropped — this feed's vocabulary is open-ended.
    assert ld._price_line(_trade(1, priceLabel="level")) == "$3.26 level"
