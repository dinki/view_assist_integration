import pytest
from datetime import datetime, timedelta
from custom_components.view_assist.timers import get_datetime_from_timer_time, TimerTime, TimerLanguage, \
    decode_time_sentence, TimerInterval
from custom_components.view_assist.translations.timers import timers_en  # Add more languages as needed

# Map languages to their corresponding modules
LANGUAGE_MODULES = {
    TimerLanguage.EN: timers_english,
   # TimerLanguage.ES: timers_spanish,  # Add more languages here
}

# Test sentences which should work in all languages
@pytest.mark.parametrize("language", LANGUAGE_MODULES.keys())
@pytest.mark.parametrize(
    "input_sentence_func,expected_output_func",
    [
        # Test intervals
        (
            lambda vars: f"5 {vars['MINUTE_PLURAL']}",
            lambda vars: TimerInterval(minutes=5),
        ),
        (
            lambda vars: f"1 {vars['MINUTE_SINGULAR']}",
            lambda vars: TimerInterval(minutes=1),
        ),
        (
            lambda vars: f"1 {vars['HOUR_SINGULAR']}",
            lambda vars: TimerInterval(hours=1),
        ),
        (
            lambda vars: f"2 {vars['HOUR_PLURAL']}",
            lambda vars: TimerInterval(hours=2),
        ),
        (
            lambda vars: f"1 {vars['DAY_SINGULAR']} 3 {vars['HOUR_PLURAL']}",
            lambda vars: TimerInterval(days=1, hours=3),
        ),
    ],
)
def test_decode_time_sentence(input_sentence_func, expected_output_func, language):
    # Load language-specific variables
    language_vars = {
        key: getattr(LANGUAGE_MODULES[language], key)
        for key in dir(LANGUAGE_MODULES[language])
        if not key.startswith("__")
    }

    # Generate input_sentence and expected_output using the language-specific variables
    input_sentence = input_sentence_func(language_vars)
    expected_output = expected_output_func(language_vars)

    # Run the test
    _, result = decode_time_sentence(input_sentence, language)
    assert result == expected_output

# Test incorrect inputs
@pytest.mark.parametrize("language", LANGUAGE_MODULES.keys())
def test_decode_time_sentence_invalid(language):
    # Load language-specific variables
    language_vars = {
        key: getattr(LANGUAGE_MODULES[language], key)
        for key in dir(LANGUAGE_MODULES[language])
        if not key.startswith("__")
    }

    # Test invalid inputs
    invalid_inputs = [
        "random text",
        "12345",
        "",
        "unknown time format",
    ]
    for sentence in invalid_inputs:
        _, result = decode_time_sentence(sentence, language)
        assert result is None

# Test function to decode time sentences
@pytest.mark.parametrize("language", LANGUAGE_MODULES.keys())
@pytest.mark.parametrize(
    "timer_time_func,expected_datetime_func",
    [
        # Test specific day (e.g., Monday)
        (
            lambda vars: TimerTime(day=vars["WEEKDAYS"][0], hour=9, minute=0, second=0, meridiem="am"),  # Monday
            lambda vars: (datetime.now() + timedelta(days=(7 - datetime.now().weekday() + 0) % 7)).replace(hour=9, minute=0, second=0, microsecond=0),
        ),
        # Test "tomorrow"
        (
            lambda vars: TimerTime(day=list(vars["SPECIAL_DAYS"].keys())[1], hour=8, minute=0, second=0, meridiem="am"),  # Tomorrow
            lambda vars: (datetime.now() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0),
        ),
        # Test "today" (rolls over to tomorrow if time has passed)
        (
            lambda vars: TimerTime(day=list(vars["SPECIAL_DAYS"].keys())[0], hour=10, minute=30, second=0, meridiem="am"),  # Today
            lambda vars: datetime.now().replace(hour=10, minute=30, second=0, microsecond=0)
            if datetime.now() < datetime.now().replace(hour=10, minute=30, second=0, microsecond=0)
            else (datetime.now() + timedelta(days=1)).replace(hour=10, minute=30, second=0, microsecond=0),
        ),
        # Test "next Tuesday"
        (
            lambda vars: TimerTime(day=f"{vars['REFERENCES']['next']} {vars['WEEKDAYS'][1]}", hour=7, minute=15, second=0, meridiem="am"),  # Next Tuesday
            lambda vars: (datetime.now() + timedelta(days=(1 - datetime.now().weekday() + 7) % 7)).replace(hour=7, minute=15, second=0, microsecond=0),
        ),
    ],
)
def test_get_datetime_from_timer_time_days(timer_time_func, expected_datetime_func, language):
    # Load language-specific variables
    language_vars = {
        key: getattr(LANGUAGE_MODULES[language], key)
        for key in dir(LANGUAGE_MODULES[language])
        if not key.startswith("__")
    }

    # Generate timer_time and expected_datetime using the language-specific variables
    timer_time = timer_time_func(language_vars)
    expected_datetime = expected_datetime_func(language_vars)

    # Run the test
    result = get_datetime_from_timer_time(timer_time, language, context_time=True)
    assert result == expected_datetime, f"Expected {expected_datetime}, but got {result}"