import datetime as dt

from agent_windows.spoken_text import local_time_reply, matches_local_time_query


def test_matches_simple_current_time_questions_only():
    assert matches_local_time_query("היי, מה השעה בישראל?")
    assert matches_local_time_query("שלום מה השעה")
    assert matches_local_time_query("תגידי לי, מה השעה עכשיו")
    assert matches_local_time_query("איזו השעה בישראל?")
    assert matches_local_time_query("השעה עכשיו!")
    assert not matches_local_time_query("מה השעה בניו יורק?")
    assert not matches_local_time_query("ספר לי על שעה בישראל")


def test_local_time_reply_is_natural_hebrew_without_seconds_or_decimals():
    fixed = dt.datetime(2026, 8, 26, 20, 54, 37, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    assert local_time_reply(fixed) == "השעה עכשיו שמונה חמישים וארבע בערב."


def test_local_time_reply_covers_minute_word_boundaries():
    expected = {
        0: "בדיוק",
        1: "אפס אחת",
        9: "אפס תשע",
        10: "עשר",
        19: "תשע עשרה",
        20: "עשרים",
        21: "עשרים ואחת",
        30: "שלושים",
        40: "ארבעים",
        50: "חמישים",
        59: "חמישים ותשע",
    }
    for minute, words in expected.items():
        fixed = dt.datetime(2026, 8, 26, 8, minute, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        assert local_time_reply(fixed) == f"השעה עכשיו שמונה {words} בבוקר."


def test_local_time_reply_covers_all_dayparts_and_twelve_hour_clock():
    cases = {
        0: "השעה עכשיו שתים עשרה בדיוק בלילה.",
        5: "השעה עכשיו חמש בדיוק בבוקר.",
        12: "השעה עכשיו שתים עשרה בדיוק בצהריים.",
        17: "השעה עכשיו חמש בדיוק בערב.",
        21: "השעה עכשיו תשע בדיוק בלילה.",
    }
    for hour, expected in cases.items():
        fixed = dt.datetime(2026, 8, 26, hour, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        assert local_time_reply(fixed) == expected
