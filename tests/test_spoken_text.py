import datetime as dt

from agent_windows.spoken_text import local_time_reply, matches_local_time_query


def test_matches_simple_current_time_questions_only():
    assert matches_local_time_query("היי, מה השעה בישראל?")
    assert matches_local_time_query("מה השעה עכשיו")
    assert matches_local_time_query("איזו השעה בישראל?")
    assert not matches_local_time_query("מה השעה בניו יורק?")
    assert not matches_local_time_query("ספר לי על שעה בישראל")


def test_local_time_reply_is_natural_hebrew_without_seconds_or_decimals():
    fixed = dt.datetime(2026, 8, 26, 20, 54, 37, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    assert local_time_reply(fixed) == "השעה עכשיו שמונה חמישים וארבע בערב."


def test_local_time_reply_says_leading_zero_minutes_naturally():
    fixed = dt.datetime(2026, 8, 26, 8, 5, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    assert local_time_reply(fixed) == "השעה עכשיו שמונה אפס חמש בבוקר."
