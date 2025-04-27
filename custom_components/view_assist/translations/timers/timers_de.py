#####################
# NEEDS TRANSLATION #
#####################
DAY_SINGULAR = "Tag"
DAY_PLURAL = "Tage"
DAY_SHORT = "d"
HOUR_SINGULAR = "Stunde"
HOUR_PLURAL = "Stunden"
HOUR_SHORT = "h"
MINUTE_SINGULAR = "Minute"
MINUTE_PLURAL = "Minuten"
MINUTE_SHORT = "m"
SECOND_SINGULAR = "Sekunde"
SECOND_PLURAL = "Sekunden"
SECOND_SHORT = "s"

WEEKDAYS = [
    "montag",
    "dienstag",
    "mittwoch",
    "donnerstag",
    "freitag",
    "samstag",
    "sonntag",
]
SPECIAL_DAYS = {
    "heute": 0,
    "morgen": 1,
}
REFERENCES = {
    "next": "nächsten",
    "tomorrow": "morgen",
    "this": "dieser",
    "at": "um",
    "and": "und",
    "to": "vor",
    "past": "nach",
}
SPECIAL_HOURS = {
    "mitternacht": 0,
    "mittag": 12,
}
HOUR_FRACTIONS = {
    "1/4": 15,
    "viertel": 15,
    "1/2": 30,
    "halb": 30,
    "3/4": 45,
    "dreiviertel": 45,
    "drei viertel": 45,
}
AMPM = ["am", "pm"]
SPECIAL_AMPM = {
    "morgens": "am",
    "heute abend": "pm",
    "nachmittags": "pm",
    "abends": "pm",
}
# Phrases that will be string replaced, irrespective of the regex
DIRECT_REPLACE = {
    "ein tag": "1 tag",
    "eine stunde": "1 stunde",
}

# Allow natural language times
# viertel nach 11
# 20 nach fünf
# halb 12
# halb zwölf
# zwanzig vor vier
# zwanzig vor vier morgens
# zwanzig vor vier abends
# 20 vor 4:00 abends
# um Mitternacht
REGEX_SUPER_TIME = (
    rf"(?i)\b(?P<{DAY_SINGULAR}>"
    + ("|".join(WEEKDAYS + list(SPECIAL_DAYS)))
    + r")?[ ]?(?:um|am)?[ ]?("
    + "|".join(list(HOUR_FRACTIONS) + list(SPECIAL_HOURS))
    + r"|[01]?[0-9]|2[0-3])\s?("
    + rf"{REFERENCES['to']}|{REFERENCES['past']})?\s?([0-5]?[0-9])?(?::[0-5][0-9])?[ ]?("
    + "|".join(AMPM + list(SPECIAL_AMPM))
    + r")?\b"
)


# All natural language intervals
# 2 1/2 Stunden
# 2 und eine halbe Stunde
# zwei und eine halbe Stunde
# eine und eine viertel Stunde
# 1 1/2 Minuten
# drei Viertel einer Stunde
# 3/4 einer Stunde
# eine halbe Stunde
# 1/2 einer Stunde
# ein Viertel einer Stunde
# 1/4 einer Stunde
REGEX_SUPER_HOUR_INTERVAL = (
    r"()(\d+)?"  # noqa: ISC003
    + r"[ ]?(?:und eine)?[ ]?("  # Übersetzung der Wortfolge
    + "|".join(HOUR_FRACTIONS)
    + r")[ ](?:einer|von einer)?[ ]?(?:stunden?)()"  # Übersetzung der Wortfolge
)

REGEX_SUPER_MIN_INTERVAL = (
    r"()()(\d+)?"  # noqa: ISC003
    + r"[ ]?(?:und eine)?[ ]?("  # Übersetzung der Wortfolge
    + "|".join(HOUR_FRACTIONS)
    + r")[ ](?:einer|von einer)?[ ]?(?:minuten?)"  # Übersetzung der Wortfolge
)

REGEX_ALT_SUPER_INTERVAL = (
    r"()"  # noqa: ISC003
    + r"(?:([01]?[0-9]|2[0-3]]|eine) stunden?)?"  # Übersetzung der Wortfolge
    + r"(?:[ ]?(?:und eine?)?[ ]?)?"  # Übersetzung der Wortfolge
    + r"("
    + "|".join(HOUR_FRACTIONS)
    + r")?()"
)

##########################
# MIGHT NEED TRANSLATION #
##########################
# This regex matches days of the week, special day references (e.g., "today", "tomorrow"), 
# and phrases like "next Monday". It is case-insensitive and ensures matches are word-bound.
# Examples of matches:
# - "Monday", "Tuesday", "Wednesday" (from WEEKDAYS)
# - "today", "tomorrow" (from SPECIAL_DAYS)
# - "next Monday", "next Friday" (constructed using REFERENCES['next'] and WEEKDAYS)
REGEX_DAYS = (
    r"(?i)\b("
    + (
        "|".join(WEEKDAYS + list(SPECIAL_DAYS))
        + "|"
        + "|".join(f"{REFERENCES['next']} {weekday}" for weekday in WEEKDAYS) # Might need translating
    )
    + ")"
)

# Find a time in the string and split into day, hours, mins and secs
# 10:15 AM
# 1600
# 15:24
# Monday at 10:00 AM
REGEX_TIME = (
    r"(?i)\b("
    + ("|".join(WEEKDAYS + list(SPECIAL_DAYS)))
    + "|"
    + ("|".join([f"{REFERENCES['next']} {day}" for day in WEEKDAYS]))  # Might need translating
    + rf")?[ ]?(?:{REFERENCES['at']})?[ ]?([01]?[0-9]|2[0-3]):?([0-5][0-9])(?::([0-9][0-9]))?[ ]?(?:{REFERENCES['this']})?[ ]?("  # Wording or Word Sequence needs translating
    + "|".join(AMPM + list(SPECIAL_AMPM))
    + r")?\b"
)
REGEX_ALT_TIME = (
    r"(?i)\b("
    + ("|".join(WEEKDAYS + list(SPECIAL_DAYS)))
    + "|"
    + ("|".join([f"{REFERENCES['next']} {day}" for day in WEEKDAYS]))
    + r")?[ ]?(?:um|am)?[ ]?"
    + r"("
    + "|".join(list(SPECIAL_HOURS))
    + r")()()()"
)

#########################
# LIKELY NO TRANSLATION #
#########################

# Find an interval in human readbale form and decode into days, hours, minutes, seconds.
# 5 minutes 30 seconds
# 5 minutes
# 2 hours 30 minutes
# 30 seconds
# 2 days 1 hour 20 minutes
# 1 day 20 minutes
# 5m
# 2h
# 1d 3h
# 30s
# 2d 1h 20m
REGEX_INTERVAL = (
    r"(?i)\b"
    rf"(?:(?P<{DAY_PLURAL}>\d+)\s*(?:{DAY_SHORT}|{DAY_PLURAL}?))?\s*"
    rf"(?:(?P<{HOUR_PLURAL}>\d+)\s*(?:{HOUR_SHORT}|{HOUR_PLURAL}?))?\s*"
    rf"(?:(?P<{MINUTE_PLURAL}>\d+)\s*(?:{MINUTE_SHORT}|{MINUTE_PLURAL}?))?\s*"
    rf"(?:(?P<{SECOND_PLURAL}>\d+)\s*(?:{SECOND_SHORT}|{SECOND_PLURAL}?))?"
    r"\b"
)

# Regex to detect intervals in a string
INTERVAL_DETECTION_REGEX = (
    rf"(?i)\b\d+\s*("
    rf"{DAY_SHORT}|{DAY_SINGULAR}|{DAY_PLURAL}|"
    rf"{HOUR_SHORT}|{HOUR_SINGULAR}|{HOUR_PLURAL}|"
    rf"{MINUTE_SHORT}|{MINUTE_SINGULAR}|{MINUTE_PLURAL}|"
    rf"{SECOND_SHORT}|{SECOND_SINGULAR}|{SECOND_PLURAL}"
    r")\b"
)

# Dictionary to hold all regexes of this language
REGEXES = {
    "interval": {
        "base": REGEX_INTERVAL,
        "super_hour": REGEX_SUPER_HOUR_INTERVAL,
        "super_min": REGEX_SUPER_MIN_INTERVAL,
        "alt_super": REGEX_ALT_SUPER_INTERVAL,
    },
    "time": {
        "base": REGEX_TIME,
        "alt_base": REGEX_ALT_TIME,
        "super": REGEX_SUPER_TIME,
    },
}

# Dictionary to hold all singulars of this language
SINGULARS = {
    "day": DAY_SINGULAR,
    "hour": HOUR_SINGULAR,
    "minute": MINUTE_SINGULAR,
    "second": SECOND_SINGULAR,
}

# Dictionary to hold all plural forms of this language
PLURAL_MAPPING = {
    DAY_SINGULAR: DAY_PLURAL,
    HOUR_SINGULAR: HOUR_PLURAL,
    MINUTE_SINGULAR: MINUTE_PLURAL,
    SECOND_SINGULAR: SECOND_PLURAL,
}