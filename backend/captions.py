"""The text of the Telegram alert of a behaviour event (a fall, a fight, someone in a restricted area).

PERSON_DETECTED keeps the caption of TelegramNotifier (src/notifications); this is for the events of ROADMAP_V2 V2-3, whose
alert has to say what happened, where, and when it began (a fall is reported two seconds after the impact).
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence


def _clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M:%S")   # the server's local time, as the PERSON_DETECTED message does


def behaviour_caption(type_: str, camera_name: str, track_ids: Sequence[int], zone_id: int | None, timestamp: datetime,
                      occurred_at: datetime | None, score: float, verification: str = "not_required",
                      verification_reason: str | None = None) -> str:
    people = " and ".join(f"#{t}" for t in track_ids)
    if type_ == "FALL":
        head, what = "🚨 FALL DETECTED", f"Person {people} fell and has not got up"
    elif type_ == "FIGHT":
        head, what = "🚨 POSSIBLE FIGHT", f"Persons {people} seem to be fighting"
    elif type_ == "RESTRICTED_AREA":
        head, what = "⛔ RESTRICTED AREA", f"Person {people} is in restricted zone {zone_id}"
    else:
        head, what = "⚠️ " + type_.replace("_", " "), f"Person {people}"
    lines = [head, f"Camera: {camera_name}", what]
    if occurred_at is not None and abs((timestamp - occurred_at).total_seconds()) >= 1.0:
        lines.append(f"Began: {_clock(occurred_at)}  Reported: {_clock(timestamp)}")
    else:
        lines.append(f"Time: {_clock(timestamp)}")
    lines.append(f"Confidence: {score:.2f}")
    if verification == "confirmed":
        lines.append("Checked by the VLM: confirmed")
    elif verification == "rejected":
        lines.append("⚠️ The VLM doubts this alert" + (f": {verification_reason.split(' [')[0]}" if verification_reason else ""))
    elif verification in ("unverified", "pending"):
        lines.append("Not checked by the VLM (unavailable, unsure or too slow)")
    return "\n".join(lines)
