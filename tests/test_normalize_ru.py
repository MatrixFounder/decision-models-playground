"""The playground's own reading of Russian dates and numbers (DESIGN §14, SPIKE §2.2): the fragments mlx-vlm leaves
without a value, and the ones that must stay None because reading them would be a guess."""

from __future__ import annotations

import pytest

from app.engines.base import normalize_answer
from app.normalize_ru import normalize, normalize_date, normalize_number
from app.schemas import DecisionRequest, Question
from tests.conftest import load

EXT = DecisionRequest.model_validate(load("requests/extensions_ru.json"))

DATES = [
    ("12 ноября 2026 года", "2026-11-12"),  # SPIKE §2.2: mlx-vlm gives None
    ("12 ноября 2026", "2026-11-12"),
    ("12 ноября 2026 г.", "2026-11-12"),
    ("1 мая 2026г.", "2026-05-01"),
    ("в четверг, 12 ноября 2026 года", "2026-11-12"),
    ("12.11.2026", "2026-11-12"),  # day first, as Russian writes it (SPIKE: None, ambiguity flag)
    ("12/11/2026", "2026-11-12"),
    ("03.04.2026", "2026-04-03"),  # not 4 March
    ("  12 ноября 2026 года. ", "2026-11-12"),
    ("12 Ноября 2026", "2026-11-12"),
    ("12 ноября 2026 года, в четверг", "2026-11-12"),  # the weekday after the date, and it is that date's
    ("2026-11-12", "2026-11-12"),  # ISO is unambiguous
    ("12 ноября 2026 года.", "2026-11-12"),
]
NOT_DATES = [
    "12 ноября",  # no year in the text: never invented
    "в четверг, 12 ноября",
    "31.02.2026",  # no such day
    "13.13.2026",
    "12.11.26",  # which century is a guess
    "завтра",
    "12 нояб 2026",
    "в среду, 12 ноября 2026",  # 12 November 2026 is a Thursday: the fragment contradicts itself (review P6)
    "12 ноября 2026, среда",
    "2026-13-01",
    "1" * 200,
    "",
    None,
]
NUMBERS = [
    ("350 000", 350000),
    ("350 000 рублей", 350000),  # SPIKE §2.2: mlx-vlm gives None
    ("350 000 руб.", 350000),
    ("350 000", 350000),
    ("350 000 ₽", 350000),
    ("350 тыс.", 350000),
    ("350 тыс. руб.", 350000),
    ("1 234,56", 1234.56),
    ("1,5 млн", 1500000),
    ("2 млрд", 2_000_000_000),
    ("1.234.567", 1234567),
    ("-5", -5),
    ("$ 120", 120),
    ("5 %", 5),
    ("триста пятьдесят тысяч рублей", 350000),  # SPIKE §2.2: mlx-vlm gives None
    ("две тысячи двадцать шесть", 2026),
    ("два миллиона триста тысяч", 2300000),
    ("миллион двести тысяч", 1200000),
    ("сто двенадцать", 112),
    ("сорок два", 42),
    ("трёх", 3),
    ("тысяча", 1000),
    ("ноль", 0),
    ("пять процентов", 5),
    # digits and words together, as Russian ASR writes amounts (review P6, F1: these were 1000 and 1 300 000)
    ("3 тысячи 500 рублей", 3500),
    ("15 тысяч 750", 15750),
    ("350 тысяч 500", 350500),
    ("2 миллиона триста тысяч", 2300000),
    ("5 тысяч сто", 5100),
    ("350 000 р.", 350000),  # the unit's dot is read with the unit
    ("350 000 р", 350000),
    ("1,005 тыс.", 1005),  # decimal arithmetic: not 1004.9999999999999
    ("1,5", 1.5),
    ("350.", 350),
]
NOT_NUMBERS = [
    "1.500",  # 1.5 in English, 1500 in German: a guess either way
    "1.5",
    "12-15",
    "около трёхсот",
    "пять пять",  # not a number's order
    "тысяча миллион",
    "ноль пять",
    "двенадцать пять",
    "полтора миллиона",
    "минус пять",
    "12 ноября",
    "сто 5",  # digits inside a group of words: a guess
    "5 сто",
    "двести 50",
    ".5",  # a leading separator is not dropped
    ",5",
    "тыс",  # an abbreviation needs a number before it
    "млн",
    "1,5 млн 200",
    "3 тысячи 1500",  # a group after a scale is below 1000
    "3 тысячи 500 000",
    "1" * 400 + " млрд",  # too long to be one amount (and int() of 4 300 digits raises)
    "",
    None,
]


@pytest.mark.parametrize(("text", "iso"), DATES)
def test_dates_read(text, iso):
    assert normalize_date(text) == iso


@pytest.mark.parametrize("text", NOT_DATES)
def test_dates_that_would_be_a_guess_stay_none(text):
    assert normalize_date(text) is None


@pytest.mark.parametrize(("text", "value"), NUMBERS)
def test_numbers_read(text, value):
    got = normalize_number(text)
    assert got == pytest.approx(value) and type(got) is type(value)


@pytest.mark.parametrize("text", NOT_NUMBERS)
def test_numbers_that_would_be_a_guess_stay_none(text):
    assert normalize_number(text) is None


def test_only_dates_and_numbers_are_normalized():
    assert normalize("text", "12 ноября 2026") is None
    assert normalize("date", "12 ноября 2026") == "2026-11-12"
    assert normalize("number", "350 тыс.") == 350000


def test_a_value_the_model_left_empty_is_read_by_the_playground():
    # the recorded d1-Omni answer: number null, text «триста пятьдесят тысяч рублей» (SPIKE §2.2)
    resp = load("mlx/mlx-d1-omni/extensions_ru.json")["response"]["answers"]
    a = normalize_answer(EXT.questions["amount"], resp["amount"])
    assert a.extract.value is None and a.extract.normalized == 350000
    i, j = a.extract.span
    assert EXT.state[i:j] == a.extract.text


def test_the_models_own_value_is_never_replaced():
    q = Question(type="date", instructions="Когда?")
    a = normalize_answer(q, {"type": "date", "date": "2026-11-13", "text": "12 ноября 2026 года", "found": 0.9})
    assert a.extract.value == "2026-11-13" and a.extract.normalized is None
    b = normalize_answer(q, {"type": "date", "date": None, "text": "12 ноября 2026 года", "found": 0.9})
    assert b.extract.value is None and b.extract.normalized == "2026-11-12"
    c = normalize_answer(q, {"type": "date", "date": None, "text": "12 ноября", "found": 0.9})
    assert c.extract.normalized is None  # no year: nothing to read
    t = normalize_answer(Question(type="text", instructions="Номер?"), {"type": "text", "text": "350 000", "found": 1})
    assert t.extract.normalized is None  # a text answer is the text


def test_a_reading_that_fails_never_sinks_the_models_answer(monkeypatch):
    from app import normalize_ru

    def broken(text):
        raise ValueError("planted")

    monkeypatch.setattr(normalize_ru, "normalize_number", broken)
    q = Question(type="number", instructions="Сколько?")
    a = normalize_answer(q, {"type": "number", "number": None, "text": "350 000", "span": [0, 7], "found": 0.9})
    assert a.extract.text == "350 000" and a.extract.found == 0.9 and a.extract.normalized is None
    big = normalize_answer(q, {"type": "number", "number": None, "text": "1" * 5000, "found": 0.9})
    assert big.extract.found == 0.9 and big.extract.normalized is None
