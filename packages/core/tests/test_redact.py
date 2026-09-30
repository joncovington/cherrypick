"""Account numbers are masked before they reach a log (cherrypick.core.redact)."""

from cherrypick.core.redact import mask_account, redact_accounts

LINE = (
    'entry order (LIVE): {"ok": true, "dry_run": false, "account_number": "5WT99991", '
    '"order_id": "ORD12345678", "buying_power": {"warnings": []}}'
)


def test_the_account_number_in_a_broker_response_is_masked():
    out = redact_accounts(LINE)
    assert "5WT99991" not in out
    assert '"account_number": "****9991"' in out


def test_order_ids_and_other_tokens_are_left_alone():
    assert '"order_id": "ORD12345678"' in redact_accounts(LINE)


def test_key_value_and_camel_case_forms_are_masked_too():
    assert redact_accounts("account=5WT99991 filled") == "account=****9991 filled"
    assert redact_accounts('{"accountNumber": "5WT99991"}') == '{"accountNumber": "****9991"}'


def test_masking_is_idempotent():
    once = redact_accounts(LINE)
    assert redact_accounts(once) == once


def test_mask_account():
    assert mask_account("5WT99991") == "****9991"
    assert mask_account("12") == "****"
    assert mask_account(None) == "****"


def test_mask_account_strips_before_taking_the_last_four():
    """A value read from a file or a prompt can carry a newline; unstripped, the mask would show three
    digits and the blank. The orchestrator's copy stripped and core's did not until they were folded."""
    assert mask_account("5WX19991\n") == "****9991"
    assert mask_account("  5WX19991  ") == "****9991"
    assert mask_account("  23  ") == "****"


def test_the_orchestrator_and_the_desk_mask_with_cores_rule():
    from cherrypick.desk import policy
    from cherrypick.orchestrator import util

    from cherrypick.core import redact

    assert util.mask_account is redact.mask_account and policy.mask_account is redact.mask_account
