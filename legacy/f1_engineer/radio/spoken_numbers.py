from __future__ import annotations

import re


LAP_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
WORD_PATTERN = "|".join(LAP_WORDS)


def spoken_integer(value):
    return int(value) if value.isdecimal() else LAP_WORDS.index(value) + 1


def normalize_duration(text):
    return re.sub(r"( for (?:the )?next )(" + WORD_PATTERN + r")( laps?)$",
                  lambda match: match.group(1) + str(spoken_integer(match.group(2))) + match.group(3), text)


def region_name_key(value):
    text = " ".join(value.casefold().replace("-", " ").split()) if isinstance(value, str) else ""
    match = re.fullmatch(r"(turn|corner) (" + WORD_PATTERN + r")", text)
    return match.group(1) + " " + str(spoken_integer(match.group(2))) if match else text


def regional_reply(text):
    if not isinstance(text, str) or not 1 <= len(text.encode("utf-8")) <= 128:
        return None
    text = " ".join(text.casefold().split()).rstrip("?.!")
    numeric = re.fullmatch(r"(?:use |from )?([0-9]{1,5}(?:\.[0-9]{1,2})?) to ([0-9]{1,5}(?:\.[0-9]{1,2})?) (?:metres|meters)", text)
    if numeric:
        return (float(numeric.group(1)), float(numeric.group(2))), None
    named = re.fullmatch(r"(?:at|use) (.{1,80})", text)
    if named:
        return None, region_name_key(named.group(1))
    if re.fullmatch(r"(?:turn|corner) (?:[1-9][0-9]?|" + WORD_PATTERN + r")", text):
        return None, region_name_key(text)
    return None


def nearby_comparison(text):
    if re.fullmatch(r"(?:a )?comparison between my car and (?:the )?cars around me[?.!]*", text):
        return "my and the car ahead and the car behind last lap and last lap comparison"
    if re.fullmatch(r"compare my car with (?:the )?cars around me[?.!]*", text):
        return "my and the car ahead and the car behind last lap and last lap comparison"
    return text
