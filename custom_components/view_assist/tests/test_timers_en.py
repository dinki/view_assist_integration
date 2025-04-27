import pytest
import datetime as dt
from custom_components.view_assist.timers import decode_time_sentence, TimerTime, TimerInterval, encode_datetime_to_human, TimerLanguage

@pytest.mark.parametrize(
    "input_sentence,language,expected_output",
    [
        # Test intervals
        ("5 minutes", TimerLanguage.EN, TimerInterval(minutes=5)),
        ("1 minute", TimerLanguage.EN, TimerInterval(minutes=1)),
        ("1 hour", TimerLanguage.EN, TimerInterval(hours=1)),
        ("2 hours", TimerLanguage.EN, TimerInterval(hours=2)),
        ("1 day 3 hours", TimerLanguage.EN, TimerInterval(days=1, hours=3)),
        ("1 second", TimerLanguage.EN, TimerInterval(seconds=1)),
        ("30 seconds", TimerLanguage.EN, TimerInterval(seconds=30)),
        ("2 days 1 hour 20 minutes", TimerLanguage.EN, TimerInterval(days=2, hours=1, minutes=20)),

        # Test shorthand intervals
        ("5m", TimerLanguage.EN, TimerInterval(minutes=5)),
        ("2h", TimerLanguage.EN, TimerInterval(hours=2)),
        ("1d 3h", TimerLanguage.EN, TimerInterval(days=1, hours=3)),
        ("30s", TimerLanguage.EN, TimerInterval(seconds=30)),
        ("2d 1h 20m", TimerLanguage.EN, TimerInterval(days=2, hours=1, minutes=20)),

        # Test specific times
        ("10:30 AM", TimerLanguage.EN, TimerTime(hour=10, minute=30, meridiem="am")),
        ("quarter past 3", TimerLanguage.EN, TimerTime(hour=3, minute=15)),
        ("half past 12", TimerLanguage.EN, TimerTime(hour=12, minute=30)),
        ("20 to 4 PM", TimerLanguage.EN, TimerTime(hour=3, minute=40, meridiem="pm")),
        ("Monday at 10:00 AM", TimerLanguage.EN, TimerTime(day="monday", hour=10, minute=0, meridiem="am")),
        ("next Tuesday at 10:00 AM", TimerLanguage.EN, TimerTime(day="next tuesday", hour=10, minute=0, meridiem="am")),

        # Test special cases
        ("midnight", TimerLanguage.EN, TimerTime(hour=0, minute=0, meridiem="am")),
        ("noon", TimerLanguage.EN, TimerTime(hour=12, minute=0, meridiem="pm")),

        # Additional examples from regex comments
        ("at 10:30 AM", TimerLanguage.EN, TimerTime(hour=10, minute=30, meridiem="am")),
        ("at quarter past 3", TimerLanguage.EN, TimerTime(hour=3, minute=15)),
        ("at half past 12", TimerLanguage.EN, TimerTime(hour=12, minute=30)),
        ("at 20 to 4 PM", TimerLanguage.EN, TimerTime(hour=3, minute=40, meridiem="pm")),
        ("at midnight", TimerLanguage.EN, TimerTime(hour=0, minute=0, meridiem="am")),
        ("at noon", TimerLanguage.EN, TimerTime(hour=12, minute=0, meridiem="pm")),
    ],
)
def test_decode_time_sentence(input_sentence, language, expected_output):
    _, result = decode_time_sentence(input_sentence, language)
    assert result == expected_output


@pytest.mark.parametrize(
    "timer_type,timer_dt,language,h24format,expected_output",
    [
        # Test TimerInterval (future intervals)
        ("TimerInterval", dt.datetime.now() + dt.timedelta(days=1, hours=2, minutes=30), TimerLanguage.EN, False, "1 day 2 hours and 30 minutes"),
        ("TimerInterval", dt.datetime.now() + dt.timedelta(hours=5, minutes=15), TimerLanguage.EN, False, "5 hours and 15 minutes"),
        ("TimerInterval", dt.datetime.now() + dt.timedelta(seconds=45), TimerLanguage.EN, False, "45 seconds"),

        # Test TimerTime (specific times in 12h and 24h formats)
        ("TimerTime", dt.datetime(2023, 10, 5, 14, 30), TimerLanguage.EN, False, "Thursday at 2:30 PM"),
        ("TimerTime", dt.datetime(2023, 10, 5, 14, 30), TimerLanguage.EN, True, "Thursday at 14:30"),
    ],
)
def test_encode_datetime_to_human(timer_type, timer_dt, language, h24format, expected_output):
    result = encode_datetime_to_human(timer_type, timer_dt, language, h24format)
    assert result == expected_output