import pytest
from custom_components.view_assist.timers import decode_time_sentence, TimerTime, TimerInterval

@pytest.mark.parametrize(
    "input_sentence,expected_output",
    [
        # Test intervals
        ("5 minutes", TimerInterval(minutes=5)),
        ("2 hours", TimerInterval(hours=2)),
        ("1 day 3 hours", TimerInterval(days=1, hours=3)),
        ("30 seconds", TimerInterval(seconds=30)),
        ("2 days 1 hour 20 minutes", TimerInterval(days=2, hours=1, minutes=20)),

        # Test specific times
        ("10:30 AM", TimerTime(hour=10, minute=30, meridiem="am")),
        ("quarter past 3", TimerTime(hour=3, minute=15)),
        ("half past 12", TimerTime(hour=12, minute=30)),
        ("20 to 4 PM", TimerTime(hour=3, minute=40, meridiem="pm")),
        ("Monday at 10:00 AM", TimerTime(day="monday", hour=10, minute=0, meridiem="am")),

        # Test special cases
        ("midnight", TimerTime(hour=0, minute=0, meridiem="am")),
        ("noon", TimerTime(hour=12, minute=0, meridiem="pm")),
    ],
)
def test_decode_time_sentence(input_sentence, expected_output):
    _, result = decode_time_sentence(input_sentence)
    assert result == expected_output


def test_decode_time_sentence_invalid():
    # Test invalid inputs
    invalid_inputs = [
        "random text",
        "12345",
        "",
        "unknown time format",
    ]
    for sentence in invalid_inputs:
        _, result = decode_time_sentence(sentence)
        assert result is None