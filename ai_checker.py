"""Проверка части с предложениями через ИИ (OpenRouter, OpenAI-совместимый API).

ИИ смотрит, что именно сделал ученик: написал ли 2 утвердительных и 2 отрицательных
предложения в Past Simple с заданным глаголом, и пишет короткий разбор по-русски.
Моделей несколько (OPENROUTER_MODELS в .env): если одна перегружена или отвечает
мусором — пробуется следующая. Если ИИ недоступен совсем — работает запасная
проверка по правилам.
"""
import asyncio
import json
import logging
import re

from openai import AsyncOpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODELS

log = logging.getLogger("ai_checker")

_MAX_TOKENS = 1500      # с запасом: «рассуждающие» модели тратят токены до ответа
_ROUND_PAUSE_SEC = 10   # если весь список моделей не ответил — пауза и ещё круг


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is None and OPENROUTER_API_KEY:
        _client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": "https://github.com/english-drill-bot",
                "X-Title": "English Drill Bot",
            },
        )
    return _client


SYSTEM_PROMPT = (
    "Ты — строгий, но доброжелательный учитель английского языка. Ты проверяешь домашнее "
    "задание ученика. Отвечай ТОЛЬКО валидным JSON без markdown и без пояснений вокруг."
)

PROMPT = """Задание ученика было таким:
Написать 2 утвердительных и 2 отрицательных предложения в Past Simple, используя неправильный глагол "{base}" (II форма: {past}, III форма: {pp}).

Ответ ученика:
\"\"\"{answer}\"\"\"

Считай ответ хорошим (sentences_ok = true), ТОЛЬКО если:
- есть ровно 2 утвердительных предложения в Past Simple с правильной формой прошедшего времени;
- есть ровно 2 отрицательных предложения в Past Simple (didn't / did not + инфинитив);
- глагол "{base}" реально использован в прошедшем времени;
- предложения грамматически связные и осмысленные.
Незаконченное по количеству (меньше 4 предложений) — не считается хорошим.
Мелкие опечатки и пропущенные точки ошибкой не считай.

Верни JSON строго вида:
{{"sentences_ok": true, "feedback": "Разбор ученику на русском: что хорошо, какие ошибки и как правильно (2-4 предложения)."}}"""


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def _ask(client: AsyncOpenAI, model: str, prompt: str) -> dict | None:
    """Один запрос к одной модели. Возвращает распарсенный JSON или None."""
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=_MAX_TOKENS,
    )
    msg = resp.choices[0].message
    data = _extract_json(msg.content or "")
    if data is None:  # рассуждающие модели иногда кладут ответ в reasoning
        data = _extract_json(getattr(msg, "reasoning", "") or "")
    return data


async def check_sentences(base: str, past: str, pp: str, answer: str) -> tuple[bool, str, bool]:
    """Возвращает (предложения_хорошие, разбор_ИИ, использован_ли_ИИ)."""
    client = _get_client()
    if client is None:
        log.info("OpenRouter key is not set, using rule-based fallback")
        return (*_rule_based_check(base, past, pp, answer), False)

    prompt = PROMPT.format(base=base, past=past, pp=pp, answer=answer[:2000])
    had_error = False

    for round_no in range(2):  # второй круг — если весь список словился на 429
        for model in OPENROUTER_MODELS:
            try:
                data = await _ask(client, model, prompt)
            except Exception as exc:  # 429/сеть/недоступность конкретной модели
                had_error = True
                log.warning("Model %s failed: %s", model, exc)
                continue
            if data is not None and "sentences_ok" in data:
                ok = bool(data["sentences_ok"])
                feedback = str(data.get("feedback", "")).strip() or (
                    "Хорошая работа!" if ok else "Есть ошибки — посмотри тему Past Simple ещё раз."
                )
                return ok, feedback, True
            log.warning("Model %s returned unexpected payload: %r", model, data)
        if round_no == 0 and had_error:
            await asyncio.sleep(_ROUND_PAUSE_SEC)

    return (*_rule_based_check(base, past, pp, answer), False)


def _rule_based_check(base: str, past: str, pp: str, answer: str) -> tuple[bool, str]:
    """Запасная проверка без ИИ: считаем предложения и ищем формы глагола."""
    text = answer.lower()
    parts = [p for p in re.split(r"[.!?\n;]", text) if p.strip()]
    if len(parts) < 4:  # возможно, разделил запятыми
        parts = [p for p in re.split(r",", text) if p.strip()]

    negatives = ("didn't", "did not", "didnt", "wasn't", "was not", "wasnt",
                 "weren't", "were not", "werent")
    neg_count = sum(1 for p in parts if any(neg in p for neg in negatives))

    past_forms = [f for f in (past, pp) if "/" not in f] + \
                 [alt for f in (past, pp) if "/" in f for alt in f.split("/")]
    used_past = any(f in p for f in past_forms for p in parts if not any(neg in p for neg in negatives))

    ok = len(parts) >= 4 and neg_count >= 2 and used_past
    if ok:
        feedback = ("Правильно: 4 предложения, есть 2 отрицательных, глагол в прошедшем времени. "
                    "(Проверено по правилам: ИИ сейчас недоступен.)")
    else:
        problems = []
        if len(parts) < 4:
            problems.append(f"нужно 4 предложения, я нашёл(ла) {len(parts)}")
        if neg_count < 2:
            problems.append("отрицательных меньше двух (didn't + инфинитив)")
        if not used_past:
            problems.append(f"не видно глагола в прошедшем времени ({past})")
        feedback = "Пока не засчитано: " + "; ".join(problems) + \
                   ". (Проверено по правилам: ИИ сейчас недоступен.)"
    return ok, feedback
