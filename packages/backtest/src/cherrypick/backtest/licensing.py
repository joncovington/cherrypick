"""The licence clock. Pure over the config and a date.

ThetaData's subscriber agreement (§8(e)) requires everything obtained from it to be expunged within
30 days of termination. The suite cannot see the subscription end, so the operator records it in
`vendor.license.terminated_on`; from then on this reports the purge deadline, and the console surface
and any fetch treat the licence as lapsed.
"""

from __future__ import annotations

from datetime import date, timedelta


def status(settings: dict, today: date | None = None) -> dict:
    today = today or date.today()
    lic = settings["vendor"]["license"]
    ended = lic.get("terminated_on")
    if not ended:
        return {"active": True, "terminated_on": None, "purge_by": None, "days_left": None, "overdue": False}
    end = date.fromisoformat(str(ended))
    purge_by = end + timedelta(days=int(lic.get("purge_within_days", 30)))
    days_left = (purge_by - today).days
    return {
        "active": today < end,
        "terminated_on": end.isoformat(),
        "purge_by": purge_by.isoformat(),
        "days_left": days_left,
        "overdue": days_left < 0,
    }
