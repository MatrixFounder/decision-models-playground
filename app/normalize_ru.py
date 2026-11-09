"""The playground's own reading of Russian dates and numbers that mlx-vlm's parser leaves empty (TASK UC-7, SPIKE §2.2).

The model finds the right fragment («12 ноября 2026 года», «триста пятьдесят тысяч рублей»), and mlx-vlm returns no
value for it. These functions read such fragments. They never guess: a fragment they cannot read whole and without
ambiguity gives None. The UI marks every value they produce as normalized by the playground, so it is never taken for
the model's own.

- dates: «12 ноября 2026 (года|г.)», a weekday before it, «12.11.2026» / «12/11/2026» read day first (Russian usage);
  a day and month without a year give None (the year is not in the text);
- numbers: digits with spaces or narrow spaces as thousands separators and a comma for decimals, «тыс.», «млн»,
  «млрд», a currency or unit around it, and numbers written in words up to the billions. Dots separate thousands only
  when there are two or more groups («1.234.567»): «1.500» reads as 1.5 in English and as 1500 in German, so it is None.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
UNITS = (r"(?:рубл(?:ей|я|ь)|руб\.?|р\.?|₽|доллар(?:ов|а)?|\$|евро|€|"
         r"процент(?:ов|а)?|%|штук(?:и)?|шт\.?|копе(?:ек|йки|йка))")
SCALES = {"тыс": 1_000, "тысяч": 1_000, "тысячи": 1_000, "тысяча": 1_000, "тысячу": 1_000,
          "млн": 1_000_000, "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000,
          "млрд": 1_000_000_000, "миллиард": 1_000_000_000, "миллиарда": 1_000_000_000, "миллиардов": 1_000_000_000}
WORDS = {
    # «ё» is read as «е» before lookup (_clean)
    "ноль": 0, "один": 1, "одна": 1, "одну": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3,
    "четыре": 4, "четырех": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
    "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700,
    "восемьсот": 800, "девятьсот": 900,
}


def _clean(text: str) -> str:
    """Spaces of every kind made one, «ё» read as «е»; trailing punctuation dropped. A leading «.» or «,» stays: «.5» is
    not 5 (review P6)."""
    s = re.sub(r"\s+", " ", text.replace(" ", " ").replace(" ", " ").replace("ё", "е")).strip()
    return re.sub(r"[,;:]+$", "", s).strip()


WEEKDAYS = {"понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3, "пятница": 4, "пятницу": 4,
            "суббота": 5, "субботу": 5, "воскресенье": 6}
WEEKDAY_RE = r"(?:во?\s+)?(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье)"
MAX_TEXT = 80  # a date or an amount is short; past this the fragment is not one value (and int() of 4 300 digits raises)


def normalize_date(text: str | None) -> str | None:
    """An ISO date, or None when the fragment is not one whole, unambiguous date. A weekday, before or after the
    date, must be that date's: «в среду, 12 ноября 2026» is None, since 12 November 2026 is a Thursday."""
    if not text or len(text) > MAX_TEXT:
        return None
    s = _clean(text).lower().rstrip(".")
    weekday = None
    m = re.match(WEEKDAY_RE + r",?\s+", s)
    if m:
        weekday, s = WEEKDAYS[m.group(1)], s[m.end():]
    m = re.search(r",?\s+" + WEEKDAY_RE + "$", s)
    if m:
        if weekday is not None:
            return None
        weekday, s = WEEKDAYS[m.group(1)], s[:m.start()]
    s = re.sub(r"\s*(?:года|год|г\.?)$", "", s)
    m = re.fullmatch(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", s)
    if m and m.group(2) in MONTHS:
        day, month, year = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    elif m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):  # ISO: unambiguous
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif m := re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))  # day first, as Russian writes it
    else:
        return None
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if weekday is not None and d.weekday() != weekday:
        return None
    return d.isoformat()


def _rank(n: int) -> int:
    """Hundreds 3, tens 2, units and teens 1: inside a group each word is of a lower rank than the one before."""
    return 3 if n >= 100 else 2 if n >= 20 else 1


SCALE_ABBR = ("тыс", "млн", "млрд")  # an abbreviation needs a number before it: «тыс» alone is not 1000
# one number as written: thousands by spaces, or by dots in two groups or more; a decimal comma
NUMBER_RE = r"-?(?:\d{1,3}(?: \d{3})+|\d{1,3}(?:\.\d{3}){2,}|\d+)(?:,\d+)?"
TOKEN_RE = re.compile(r"\s*(?:(" + NUMBER_RE + r")|([а-я]+)\.?)")


def _tokens(s: str) -> list[tuple[str, str]] | None:
    """The fragment as ("num", "350 000") and ("word", "тысяч") tokens, or None if anything else is in it."""
    out, pos = [], 0
    while pos < len(s):
        m = TOKEN_RE.match(s, pos)
        if not m or m.end() == pos:
            return None
        out.append(("num", m.group(1)) if m.group(1) is not None else ("word", m.group(2)))
        pos = m.end()
    return out


def _decimal(token: str) -> Decimal:
    whole, _, frac = token.partition(",")
    whole = whole.replace(" ", "")
    if whole.count(".") >= 2:
        whole = whole.replace(".", "")
    return Decimal(f"{whole}.{frac}" if frac else whole)


def _parse(tokens: list[tuple[str, str]]) -> Decimal | None:
    """Groups of a number, each ended by a scale word in descending order: «3 тысячи 500» → 3500, «2 миллиона триста
    тысяч» → 2 300 000. A group is one digit number or number words in a number's order, never both («сто 5» → None).
    A group after a scale is below 1000; a decimal number stands alone or before one scale («1,5 млн»)."""
    total = Decimal(0)
    group: Decimal | None = None
    kind = None  # "num" | "words"
    rank, scale = 4, None
    nums = sum(1 for k, _ in tokens if k == "num")
    for k, v in tokens:
        if k == "num":
            if group is not None:
                return None
            group, kind = _decimal(v), "num"
            if "," in v and (nums > 1 or len(tokens) > 2):
                return None
        elif v in WORDS:
            n = WORDS[v]
            if kind == "num" or _rank(n) >= rank or n == 0 and len(tokens) > 1:
                return None
            group, kind, rank = (group or Decimal(0)) + n, "words", _rank(n)
        elif v in SCALES:
            sc = SCALES[v]
            if scale is not None and (sc >= scale or group is not None and group >= 1000):
                return None
            if group is None:
                if v in SCALE_ABBR:
                    return None
                group = Decimal(1)  # «тысяча», «миллион» alone are one of them
            total += group * sc
            group, kind, rank, scale = None, None, 4, sc
        else:
            return None
    if group is not None:
        if scale is not None and group >= 1000:
            return None
        total += group
    return total


def normalize_number(text: str | None) -> float | int | None:
    """The number the fragment says, or None when it does not read as one number."""
    if not text or len(text) > MAX_TEXT:
        return None
    s = _clean(text).lower()
    s = re.sub(r"\s*" + UNITS + r"$", "", s).strip()  # the unit before its dot is taken: «350 000 р.»
    s = re.sub(r"^" + UNITS + r"\s*", "", s).strip()  # «$ 120»
    s = s.rstrip(".").strip()
    tokens = _tokens(s) if s else None
    if not tokens:
        return None
    value = _parse(tokens)
    if value is None or not value.is_finite():
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def normalize(qtype: str, text: str | None):
    """The playground's value for an extraction the model left without one (date and number only). A helper must not
    decide whether the model's answer counts: any error here is "no reading", never `invalid_answer` (review P6)."""
    try:
        if qtype == "date":
            return normalize_date(text)
        if qtype == "number":
            return normalize_number(text)
    except Exception:  # noqa: BLE001 — a reading the playground cannot make is simply not made
        return None
    return None
