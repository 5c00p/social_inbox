"""Tests for safety filters: banned patterns, symptom detection, operator keywords."""
from __future__ import annotations

import pytest

from app.services.safety import check_incoming, check_outgoing

# ---- Outgoing: banned medical claims ----

@pytest.mark.parametrize("phrase, expected_label_substr", [
    ("Это масло лечит головную боль", "лечит"),
    ("Лаванда вылечит бессонницу", "вылечит"),
    ("Профилактика рака с маслами", "профилактика"),
    ("Замените лекарства на масла", "замена лекарств"),
    ("Можно вместо таблеток", "вместо таблеток"),
    ("Гарантирую результат за неделю", "гарантирую"),
    ("100% результат через месяц", "100"),
    ("Эфирные масла как антибиотики", "антибиотик"),
    ("Можно принимать внутрь без консультации", "опасное"),
    ("У вас диагноз — стресс", "диагностика"),
])
def test_outgoing_blocks_medical_claims(phrase: str, expected_label_substr: str) -> None:
    result = check_outgoing(phrase)
    assert result.verdict == "blocked", f"Should block: {phrase!r}"
    assert result.reason is not None
    assert expected_label_substr.lower() in result.reason.lower()


@pytest.mark.parametrize("phrase", [
    "Программа очищения — это 30 дней с маслами",
    "Эфирные масла doTERRA имеют сертификат CPTG",
    "Лаванда часто помогает расслабиться",
    "Многие используют масла для ухода за кожей",
    "Юля проводит онлайн-консультации",
    "В программу входят масла и бады",
])
def test_outgoing_allows_safe_phrases(phrase: str) -> None:
    result = check_outgoing(phrase)
    assert result.verdict == "ok", f"Should pass: {phrase!r}, got: {result.reason}"


def test_outgoing_empty_text_is_ok() -> None:
    assert check_outgoing("").verdict == "ok"


# ---- Incoming: operator request ----

@pytest.mark.parametrize("phrase", [
    "Хочу с оператором поговорить",
    "Позовите человека пожалуйста",
    "Можно с Юлей лично пообщаться?",
    "I want to talk to a human",
    "Need an agent",
    "Нужен администратор",
])
def test_incoming_detects_operator_request(phrase: str) -> None:
    result = check_incoming(phrase)
    assert result.trigger == "operator_request", f"Failed for: {phrase!r}"


# ---- Incoming: symptoms ----

@pytest.mark.parametrize("phrase", [
    "У меня болит голова",
    "Боль в спине, что взять?",
    "Мне поставили диагноз диабет",
    "Я беременна, можно ли масла?",
    "Кормлю грудью, какие масла безопасны?",
    "Был у врача, прописали лекарства",
    "Принимаю таблетки от давления",
])
def test_incoming_detects_symptom(phrase: str) -> None:
    result = check_incoming(phrase)
    assert result.trigger == "symptom", f"Failed for: {phrase!r}"


@pytest.mark.parametrize("phrase", [
    "Расскажи про программу",
    "Какие масла любимые?",
    "Хочу попробовать очищение",
    "Сколько стоят пробники?",
])
def test_incoming_passes_normal_questions(phrase: str) -> None:
    result = check_incoming(phrase)
    assert result.trigger == "none", f"False positive on: {phrase!r}"


def test_incoming_empty_text() -> None:
    assert check_incoming(None).trigger == "none"
    assert check_incoming("").trigger == "none"


def test_operator_request_priority_over_symptom() -> None:
    """If both keywords present, operator_request wins (we want to ack the user)."""
    result = check_incoming("Хочу к оператору, у меня болит спина")
    assert result.trigger == "operator_request"
