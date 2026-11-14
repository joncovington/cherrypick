"""The licence clock. Pure over the config and a date.

ThetaData's subscriber agreement (§8(e)) requires everything obtained from it to be expunged within
30 days of termination. The suite cannot see the subscription end, so the operator records it in
`vendor.license.terminated_on`; from then on this reports the purge deadline, and the console surface
and any fetch treat the licence as lapsed.

**What happens at the end (the user's decision, 2026-11-14).** The stores and every result derived
from them are kept through the 30 days, then reviewed once, then deleted:

- `keeping`: from the termination to `review_from`, nothing changes; no fetch runs.
- `final_review`: the last `REVIEW_DAYS` before `purge_by`, the studies' results are read one last
  time (verdicts are already in the repo, without figures; anything still worth knowing is written
  up the same way), and nothing new is queued.
- `purge_due` on `purge_by`, `overdue` after it: `purge --derived --apply` removes the stores and the
  results together. A recorded verdict stands without its data, read as "not reproducible".
"""

from __future__ import annotations

from datetime import date, timedelta

#: The last days before the purge deadline given to the final review.
REVIEW_DAYS = 7


def status(settings: dict, today: date | None = None) -> dict:
    today = today or date.today()
    lic = settings["vendor"]["license"]
    ended = lic.get("terminated_on")
    if not ended:
        return {
            "active": True,
            "terminated_on": None,
            "purge_by": None,
            "review_from": None,
            "phase": "active",
            "days_left": None,
            "overdue": False,
        }
    end = date.fromisoformat(str(ended))
    purge_by = end + timedelta(days=int(lic.get("purge_within_days", 30)))
    review_from = max(end, purge_by - timedelta(days=REVIEW_DAYS))
    days_left = (purge_by - today).days
    if today < end:
        phase = "active"
    elif days_left < 0:
        phase = "overdue"
    elif today >= purge_by:
        phase = "purge_due"
    elif today >= review_from:
        phase = "final_review"
    else:
        phase = "keeping"
    return {
        "active": today < end,
        "terminated_on": end.isoformat(),
        "purge_by": purge_by.isoformat(),
        "review_from": review_from.isoformat(),
        "phase": phase,
        "days_left": days_left,
        "overdue": days_left < 0,
    }
