"""Convert time words to numbers."""

import re

numbers = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100",
    "thousand": "1000",
    "million": "1000000",
    "billion": "1000000000",
}


class WordsToDigits:
    """Convert number words to digits in a string."""

    @staticmethod
    def convert(s: str, number_joiner: str | None = None) -> str:
        """Convert number words to digits in a string."""
        s = s.lower()

        # Handle "twenty one", "thirty two", etc.
        tens = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"

        units = "one|two|three|four|five|six|seven|eight|nine"

        word_pattern = rf"(?:^|\s)({tens}) ({units})(?:$|\s)"
        if matches := re.findall(word_pattern, s):
            for m in matches:
                p = rf"(?:^|\s)({m[0]}) ({m[1]})(?:$|\s)"
                s = re.sub(
                    p,
                    " " + str(int(numbers[m[0]]) + int(numbers[m[1]])) + " ",
                    s,
                )

        all_numbers = "|".join(numbers.keys())
        word_pattern = rf"({all_numbers})\b"
        if m := re.findall(word_pattern, s):
            for group in m:
                word_p = rf"(?:^|\s)({group})(?:$|\s)"
                s = re.sub(word_p, " " + numbers[group] + " ", s)

        # Clean up spaces
        s = re.sub(r"\s+", " ", s)
        return s.strip()
