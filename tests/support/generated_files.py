import datetime
import json

TYPES_FILE = "frontend/src/lib/api/types.ts"
MANDATORY_JOB = "checks"


def generated_config(
    path: str | None = TYPES_FILE,
    job: str | None = MANDATORY_JOB,
    item: str | None = "#174",
    date: datetime.date | str | None = datetime.date(2026, 10, 30),
    reason: str | None = "written by scripts/generate_types.py, proven by its --check",
) -> str:
    """One [[size.generated]] entry; a string is written as a TOML string, a date as a TOML date, None drops the key."""
    values = {"path": path, "job": job, "item": item, "date": date, "reason": reason}
    entries = "".join(
        f"{key} = {value.isoformat() if isinstance(value, datetime.date) else json.dumps(value)}\n"
        for key, value in values.items() if value is not None
    )
    return "[[size.generated]]\n" + entries
