"""Produce truthful publication records; publishing requires matching public evidence."""

from datetime import datetime, timezone

STATES = {
    "prepared",
    "submitted",
    "under_review",
    "published",
    "requires_user_action",
    "failed",
}


def publication_record(
    platform,
    status,
    *,
    version,
    url=None,
    public_record=None,
    required_user_action=None,
    evidence=None,
):
    if status not in STATES:
        raise ValueError("Unknown publication state")
    if status == "published" and (
        not url
        or not public_record
        or public_record.get("version") != version
        or public_record.get("name") != "io.github.Mola-maker/cad2gis-agent"
    ):
        raise ValueError("Published requires matching public name/version readback")
    if status == "requires_user_action" and not required_user_action:
        raise ValueError("Missing required user action")
    return {
        "platform": platform,
        "status": status,
        "url": url,
        "version": version,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            **(evidence or {}),
            **({"public_record": public_record} if public_record else {}),
        },
        "required_user_action": required_user_action,
    }
