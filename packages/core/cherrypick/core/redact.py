"""Account numbers masked to `****1234` before they reach a log (the suite guardrail).

A broker response carries the full account number (`"account_number": "5WT99991"`), and a module
that logs the response whole -- flies' live loop logged every order placement's that way -- writes it
into a plain-text file that is read, tailed, pasted and archived. Masking at the log sink, rather than
at each call site, covers a line nobody has written yet.

Only the value of a key that NAMES an account is masked (`account_number`, `account-number`,
`accountNumber`, `account`), in JSON or `key=value` form. A bare token that happens to look like an
account number is left alone: guessing at arbitrary eight-character strings would mangle order ids and
symbols in the same lines, which are what the log is for.
"""

from __future__ import annotations

import re
from typing import Any

_KEY = r"account[_-]?number|accountNumber|account"
# "account_number": "5WT99991"  |  'account': '5WT99991'  |  account_number=5WT99991
_JSON = re.compile(rf"""(["']?(?:{_KEY})["']?\s*:\s*["'])([A-Za-z0-9-]{{5,}})(["'])""")
_KV = re.compile(rf"""(\b(?:{_KEY})=)([A-Za-z0-9-]{{5,}})\b""")


def mask_account(value: Any) -> str:
    """`****` plus the last four characters, or `****` alone for anything shorter -- the one masking
    rule (orchestrator and desk import it). Stripped first: an account number read from a file or a
    prompt with a trailing newline would otherwise show three digits and a blank."""
    s = "" if value is None else str(value).strip()
    return f"****{s[-4:]}" if len(s) >= 4 else "****"


def redact_accounts(text: str) -> str:
    """`text` with every value of an account-naming key masked. Idempotent: an already-masked value
    (`****9991`) contains no alphanumeric run the patterns match, so it passes through unchanged."""
    text = _JSON.sub(lambda m: f"{m.group(1)}{mask_account(m.group(2))}{m.group(3)}", text)
    return _KV.sub(lambda m: f"{m.group(1)}{mask_account(m.group(2))}", text)
