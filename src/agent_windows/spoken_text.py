from __future__ import annotations

import datetime as dt
import re


_TIME_QUERY_RE = re.compile(
    r"^(?:(?:היי|שלום)[, ]+)?(?:תגיד(?:י)? לי[ ,]+)?"
    r"(?:(?:מה|איזו|איזה)\s+השעה|השעה\s+עכשיו)"
    r"(?:\s+(?:עכשיו|בישראל))?(?:\s+בישראל)?[?.! ]*$",
    re.IGNORECASE,
)

_HOUR_WORDS = {
    0: "שתים עשרה",
    1: "אחת",
    2: "שתיים",
    3: "שלוש",
    4: "ארבע",
    5: "חמש",
    6: "שש",
    7: "שבע",
    8: "שמונה",
    9: "תשע",
    10: "עשר",
    11: "אחת עשרה",
}
_ONES = {
    1: "אחת",
    2: "שתיים",
    3: "שלוש",
    4: "ארבע",
    5: "חמש",
    6: "שש",
    7: "שבע",
    8: "שמונה",
    9: "תשע",
}
_TEENS = {
    10: "עשר",
    11: "אחת עשרה",
    12: "שתים עשרה",
    13: "שלוש עשרה",
    14: "ארבע עשרה",
    15: "חמש עשרה",
    16: "שש עשרה",
    17: "שבע עשרה",
    18: "שמונה עשרה",
    19: "תשע עשרה",
}
_TENS = {20: "עשרים", 30: "שלושים", 40: "ארבעים", 50: "חמישים"}


def matches_local_time_query(text: str) -> bool:
    """Recognize only simple questions about the host/Israel current time."""
    normalized = " ".join(str(text).strip().split())
    return bool(_TIME_QUERY_RE.fullmatch(normalized))


def _minute_words(minute: int) -> str:
    if minute == 0:
        return "בדיוק"
    if minute < 10:
        return "אפס " + _ONES[minute]
    if minute < 20:
        return _TEENS[minute]
    tens = (minute // 10) * 10
    ones = minute % 10
    return _TENS[tens] if ones == 0 else f"{_TENS[tens]} ו{_ONES[ones]}"


def _daypart(hour: int) -> str:
    if 5 <= hour < 12:
        return "בבוקר"
    if 12 <= hour < 17:
        return "בצהריים"
    if 17 <= hour < 21:
        return "בערב"
    return "בלילה"


def local_time_reply(now: dt.datetime | None = None) -> str:
    """Return a TTS-friendly Hebrew clock answer sourced from the host clock."""
    current = now or dt.datetime.now().astimezone()
    hour12 = current.hour % 12
    return f"השעה עכשיו {_HOUR_WORDS[hour12]} {_minute_words(current.minute)} {_daypart(current.hour)}."
