#####################
# NEEDS TRANSLATION #
#####################
DAY_SINGULAR = "day"
DAY_PLURAL = "days"
DAY_SHORT = "d"
HOUR_SINGULAR = "hour"
HOUR_PLURAL = "hours"
HOUR_SHORT = "h"
MINUTE_SINGULAR = "minute"
MINUTE_PLURAL = "minutes"
MINUTE_SHORT = "m"
SECOND_SINGULAR = "second"
SECOND_PLURAL = "seconds"
SECOND_SHORT = "s"

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
SPECIAL_DAYS = {
    "today": 0,
    "tomorrow": 1,
}
REFERENCES = {
    "next": "next",
    "tomorrow": "tomorrow",
    "this": "this",
    "at": "at",
    "and": "and",
}
SPECIAL_HOURS = {
    "midnight": 0,
    "noon": 12,
}
HOUR_FRACTIONS = {
    "1/4": 15,
    "quarter": 15,
    "1/2": 30,
    "half": 30,
    "3/4": 45,
    "three quarters": 45,
}
AMPM = ["am", "pm"]
SPECIAL_AMPM = {
    "morning": "am",
    "tonight": "pm",
    "afternoon": "pm",
    "evening": "pm",
}
# Phrases that will be string replaced, irrespective of the regex
DIRECT_REPLACE = {
    "a day": "1 day",
    "an hour": "1 hour",
}

# Allow natural language times
# quarter past 11
# 20 past five
# half past 12
# half past twelve
# twenty to four
# twenty to four AM
# twenty to four PM
# 20 to 4:00 PM
REGEX_SUPER_TIME = (
    rf"(?i)\b(?P<{DAY_SINGULAR}>"
    + ("|".join(WEEKDAYS + list(SPECIAL_DAYS)))
    + r")?[ ]?(?:at)?[ ]?(\d+|"
    + "|".join(list(HOUR_FRACTIONS))
    + r")\s(to|past)\s(\d+|" # Wording or Word Sequence needs translating
    + ("|".join(SPECIAL_HOURS))
    + r")(?::\d+)?[ ]?("
    + "|".join(AMPM + list(SPECIAL_AMPM))
    + r")?\b"
)


# All natural language intervals
# 2 1/2 hours
# 2 and a half hours
# two and a half hours
# one and a quarter hours
# 1 1/2 minutes
# three quarters of an hour
# 3/4 of an hour
# half an hour
# 1/2 an hour
# quarter of an hour
# 1/4 of an hour
REGEX_SUPER_HOUR_INTERVAL = (
    r"()(\d+)?"  # noqa: ISC003
    + r"[ ]?(?:and a)?[ ]?("  # Wording or Word Sequence needs translating
    + "|".join(HOUR_FRACTIONS)
    + r")[ ](?:an|of an)?[ ]?(?:hours?)()"  # Wording or Word Sequence needs translating
)

REGEX_SUPER_MIN_INTERVAL = (
    r"()()(\d+)?"  # noqa: ISC003
    + r"[ ]?(?:and a)?[ ]?("  # Wording or Word Sequence needs translating
    + "|".join(HOUR_FRACTIONS)
    + r")[ ](?:an|of an)?[ ]?(?:minutes?)"  # Wording or Word Sequence needs translating
)

REGEX_ALT_SUPER_INTERVAL = (
    r"()"  # noqa: ISC003
    + r"(?:([01]?[0-9]|2[0-3]]|an) hours?)?"  # Wording or Word Sequence needs translating
    + r"(?:[ ]?(?:and a?)?[ ]?)?"  # Wording or Word Sequence needs translating
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
    + ("|".join([f"{REFERENCES['next']} {day}" for day in WEEKDAYS])) # Might need translating
    + r")?[ ]?(?:at)?[ ]?"
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