import pytest
import datetime as dt
from custom_components.view_assist.timers import decode_time_sentence, TimerTime, TimerInterval, encode_datetime_to_human, TimerLanguage

@pytest.mark.parametrize(
    "input_sentence,language,expected_output",
    [
        # Testintervalle
        # ("5 Minuten", TimerLanguage.DE, TimerInterval(minutes=5)),
        # ("1 Minute", TimerLanguage.DE, TimerInterval(minutes=1)),
        # ("1 Stunde", TimerLanguage.DE, TimerInterval(hours=1)),
        # ("2 Stunden", TimerLanguage.DE, TimerInterval(hours=2)),
        # ("1 Tag 3 Stunden", TimerLanguage.DE, TimerInterval(days=1, hours=3)),
        # ("1 Sekunde", TimerLanguage.DE, TimerInterval(seconds=1)),
        # ("30 Sekunden", TimerLanguage.DE, TimerInterval(seconds=30)),
        # ("2 Tage 1 Stunde 20 Minuten", TimerLanguage.DE, TimerInterval(days=2, hours=1, minutes=20)),
        #
        # # Kurzschreibweise für Intervalle
        # ("5m", TimerLanguage.DE, TimerInterval(minutes=5)),
        # ("2h", TimerLanguage.DE, TimerInterval(hours=2)),
        # ("1d 3h", TimerLanguage.DE, TimerInterval(days=1, hours=3)),
        # ("30s", TimerLanguage.DE, TimerInterval(seconds=30)),
        # ("2d 1h 20m", TimerLanguage.DE, TimerInterval(days=2, hours=1, minutes=20)),
        #
        # # Test spezifische Zeiten
        # ("10:30 Uhr", TimerLanguage.DE, TimerTime(hour=10, minute=30)),
        # ("viertel nach 3", TimerLanguage.DE, TimerTime(hour=3, minute=15)),
        # ("halb 12", TimerLanguage.DE, TimerTime(hour=12, minute=30)),
        # ("20 vor 4", TimerLanguage.DE, TimerTime(hour=3, minute=40)),
        # ("Montag um 10:00 Uhr", TimerLanguage.DE, TimerTime(day="montag", hour=10, minute=0)),
        # ("nächsten Dienstag um 10:00 Uhr", TimerLanguage.DE, TimerTime(day="nächsten dienstag", hour=10, minute=0)),

        # # Test Sonderfälle
        # ("Mitternacht", TimerLanguage.DE, TimerTime(hour=0, minute=0)),
        # ("Mittag", TimerLanguage.DE, TimerTime(hour=12, minute=0)),
        #
        # # Zusätzliche Beispiele aus den Regex-Kommentaren
        # ("um 10:30 Uhr", TimerLanguage.DE, TimerTime(hour=10, minute=30)),
        # ("um viertel nach 3", TimerLanguage.DE, TimerTime(hour=3, minute=15)),
        # ("um halb 12", TimerLanguage.DE, TimerTime(hour=12, minute=30)),
        # ("um 20 vor 4", TimerLanguage.DE, TimerTime(hour=3, minute=40)),
        ("um Mitternacht", TimerLanguage.DE, TimerTime(hour=0, minute=0)),
        ("am Mittag", TimerLanguage.DE, TimerTime(hour=12, minute=0)),
    ],
)
def test_decode_time_sentence(input_sentence, language, expected_output):
    _, result = decode_time_sentence(input_sentence, language)
    assert result == expected_output


@pytest.mark.parametrize(
    "timer_type,timer_dt,language,h24format,expected_output",
    [
        # Test TimerInterval (zukünftige Intervalle)
        ("TimerInterval", dt.datetime.now() + dt.timedelta(days=1, hours=2, minutes=30), TimerLanguage.DE, False, "1 Tag 2 Stunden und 30 Minuten"),
        ("TimerInterval", dt.datetime.now() + dt.timedelta(hours=5, minutes=15), TimerLanguage.DE, False, "5 Stunden und 15 Minuten"),
        ("TimerInterval", dt.datetime.now() + dt.timedelta(seconds=45), TimerLanguage.DE, False, "45 Sekunden"),

        # Test TimerTime (spezifische Zeiten im 12h- und 24h-Format)
        ("TimerTime", dt.datetime(2023, 10, 5, 14, 30), TimerLanguage.DE, False, "Donnerstag um 2:30 PM"),
        ("TimerTime", dt.datetime(2023, 10, 5, 14, 30), TimerLanguage.DE, True, "Donnerstag um 14:30"),
    ],
)
def test_encode_datetime_to_human(timer_type, timer_dt, language, h24format, expected_output):
    result = encode_datetime_to_human(timer_type, timer_dt, language, h24format)
    assert result == expected_output