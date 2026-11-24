"""Telegram-бот-тренажёр неправильных глаголов.

Каждый день в TASKS_PER_DAY случайных моментах внутри окна 10:00–15:00 бот пишет
задание: три формы неправильного глагола, потом 2 утвердительных и 2 отрицательных
предложения в Past Simple. Ответы проверяет ИИ (OpenRouter), баллы начисляются
по скорости и качеству. Запуск: python bot.py
"""
import asyncio
import datetime as dt
import html
import json
import logging
import random
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

import ai_checker
import db
from config import (
    BOT_TOKEN, FAST_LIMIT_SEC, FAST_POINTS, MAX_RUN_MINUTES, MAX_SESSION_POINTS,
    MID_LIMIT_SEC, MID_POINTS, SENTENCE_POINTS, SLOW_POINTS, TASKS_PER_DAY,
    TIMEZONE, WINDOW_END_HOUR, WINDOW_START_HOUR,
)
from verbs import VERB_LIST

log = logging.getLogger("bot")
router = Router()
scheduler = AsyncIOScheduler()

# Активные задания: user_id -> {"id", "verb", "sent_at", "stage": "forms"|"sentences", ...}
ACTIVE: dict[int, dict] = {}

HELP_TEXT = (
    "🏋️ <b>Тренажёр неправильных глаголов</b>\n\n"
    "Каждый день бот сам напишет тебе задание — 2 раза в случайное время "
    "между 10:00 и 15:00.\n\n"
    "<b>Задание:</b>\n"
    "1️⃣ Написать три формы неправильного глагола (можно только II и III: "
    "<i>went, gone</i> или все три: <i>go went gone</i>)\n"
    "2️⃣ Написать 2 утвердительных и 2 отрицательных предложения в Past Simple "
    "с этим глаголом\n\n"
    "<b>Баллы за формы (если все верные):</b>\n"
    f"• быстрее {FAST_LIMIT_SEC} секунд — <b>{FAST_POINTS} баллов</b> 🏆\n"
    f"• от {FAST_LIMIT_SEC} секунд до 1 минуты — <b>{MID_POINTS} баллов</b>\n"
    f"• дольше минуты — <b>{SLOW_POINTS} баллов</b>\n"
    "• с ошибкой — 0 баллов\n\n"
    "Хороший ответ ИИ на предложения: <b>+10 баллов</b>\n"
    f"Максимум за задание: <b>{MAX_SESSION_POINTS} баллов</b>\n\n"
    "<b>Команды:</b>\n"
    "/now — тренировочное задание прямо сейчас\n"
    "/skip — пропустить текущее задание\n"
    "/stats — твоя статистика\n"
    "/top — рейтинг учеников\n"
    "/forget — удалить все мои данные\n"
    "/help — правила"
)


def tzinfo():
    if TIMEZONE:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE)
    return dt.datetime.now().astimezone().tzinfo


def now_tz() -> dt.datetime:
    return dt.datetime.now(tzinfo())


# ---------- проверка форм глагола ----------

def check_forms(verb: dict, text: str) -> bool:
    """В ответе должны быть II и III формы (порядок не важен, регистр не важен)."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    past_ok = verb["past"] in words or any(a in words for a in verb["past_alt"])
    pp_ok = verb["pp"] in words or any(a in words for a in verb["pp_alt"])
    return past_ok and pp_ok


def points_for_time(sec: float) -> int:
    if sec <= FAST_LIMIT_SEC:
        return FAST_POINTS
    if sec <= MID_LIMIT_SEC:
        return MID_POINTS
    return SLOW_POINTS


# ---------- задание ----------

async def send_task(bot: Bot, user_id: int) -> None:
    verb = random.choice(VERB_LIST)
    sent_at = time.time()
    session_id = db.start_session(user_id, verb["base"], sent_at)
    ACTIVE[user_id] = {"id": session_id, "verb": verb, "sent_at": sent_at, "stage": "forms"}
    await bot.send_message(
        user_id,
        "🔔 <b>Время тренировки!</b>\n\n"
        f"Напиши 3 формы глагола <b>{verb['base'].upper()}</b> ({verb['ru']}).\n\n"
        "⏱ До 25 сек — 25 баллов · до 1 мин — 18 · дольше — 10\n"
        "Формат: <i>went, gone</i>",
    )


# ---------- обработка ответов ----------

async def handle_forms(m: Message, sess: dict) -> None:
    verb = sess["verb"]
    elapsed = time.time() - sess["sent_at"]
    ok = check_forms(verb, m.text or "")
    pts = points_for_time(elapsed) if ok else 0
    db.save_forms(sess["id"], m.text or "", elapsed, ok, pts)
    sess.update(stage="sentences", forms_points=pts, forms_ok=ok, forms_sec=elapsed)

    if ok:
        head = (
            f"✅ Верно! {verb['base']} — {verb['past']} — {verb['pp']}\n"
            f"⏱ {elapsed:.1f} сек → <b>+{pts} баллов</b> за формы"
        )
    else:
        head = (
            f"❌ Не все формы верны. Правильно: {verb['base']} — {verb['past']} — {verb['pp']}\n"
            f"За формы — 0 баллов, но предложения ещё могут принести +10!"
        )
    await m.answer(
        head + "\n\n✍️ Теперь напиши <b>2 утвердительных и 2 отрицательных</b> "
        f"предложения в Past Simple с глаголом <b>{verb['base']}</b> "
        "(каждое с новой строки или через запятую)."
    )


async def handle_sentences(m: Message, sess: dict) -> None:
    verb = sess["verb"]
    await m.bot.send_chat_action(m.from_user.id, "typing")
    ok, feedback, ai_used = await ai_checker.check_sentences(
        verb["base"], verb["past"], verb["pp"], m.text or "",
    )
    s_pts = SENTENCE_POINTS if ok else 0
    total = sess.get("forms_points", 0) + s_pts
    db.save_sentences(sess["id"], m.text or "", ok, s_pts, feedback, total, ai_used)
    ACTIVE.pop(m.from_user.id, None)

    verdict = "✅ Отлично, +10 баллов за предложения" if ok else "❌ Предложения не засчитаны — 0 баллов"
    await m.answer(
        f"{verdict}\n\n🤖 <b>Разбор ИИ:</b>\n{html.escape(feedback)}\n\n"
        f"🏁 <b>Итого за задание: {total} из {MAX_SESSION_POINTS} баллов</b>"
    )


# ---------- команды ----------

@router.message(CommandStart())
async def cmd_start(m: Message) -> None:
    db.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    await m.answer(HELP_TEXT)


@router.message(Command("help"))
async def cmd_help(m: Message) -> None:
    await m.answer(HELP_TEXT)


@router.message(Command("now"))
async def cmd_now(m: Message) -> None:
    db.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    if m.from_user.id in ACTIVE:
        await m.answer("Сначала закончи текущее задание или отправь /skip.")
        return
    await m.answer("🎯 Тренировочное задание (те же баллы, что и в обычном):")
    await send_task(m.bot, m.from_user.id)


@router.message(Command("skip"))
async def cmd_skip(m: Message) -> None:
    sess = ACTIVE.pop(m.from_user.id, None)
    if sess:
        db.mark_skipped(sess["id"])
        await m.answer("Задание пропущено (0 баллов). Следующее придёт по расписанию 🙂")
    else:
        await m.answer("Сейчас активного задания нет.")


@router.message(Command("stats"))
async def cmd_stats(m: Message) -> None:
    db.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    s = db.get_stats(m.from_user.id)
    if s["done"] == 0:
        await m.answer("Пока нет законченных заданий. Жди задания или отправь /now 🙂")
        return
    forms_pct = round(100 * s["forms_ok"] / s["done"])
    sent_pct = round(100 * s["sent_ok"] / s["done"])
    best = f"{s['best_sec']:.1f} сек" if s["best_sec"] else "—"
    await m.answer(
        f"📊 <b>Твоя статистика</b>\n\n"
        f"Заданий пройдено: <b>{s['done']}</b>\n"
        f"Всего баллов: <b>{s['points']}</b>\n"
        f"Формы без ошибок: {s['forms_ok']} из {s['done']} ({forms_pct}%)\n"
        f"Предложения зачтены: {s['sent_ok']} из {s['done']} ({sent_pct}%)\n"
        f"Среднее время верного ответа: {s['avg_sec']:.1f} сек\n"
        f"Лучшее время: {best}"
    )


@router.message(Command("forget"))
async def cmd_forget(m: Message) -> None:
    # необратимое удаление всех данных пользователя — защищено подтверждением
    if (m.text or "").strip().lower() not in ("/forget да", "/forget yes"):
        await m.answer(
            "⚠️ <b>/forget</b> удаляет все твои баллы, статистику и ответы безвозвратно.\n"
            "Для подтверждения отправь: <code>/forget ДА</code>"
        )
        return
    deleted = db.delete_user_data(m.from_user.id)
    ACTIVE.pop(m.from_user.id, None)
    await m.answer(
        f"🗑 Удалено записей о заданиях: {deleted}.\n"
        "Все твои данные стёрты. Отправь /start, чтобы начать заново."
    )


@router.message(Command("top"))
async def cmd_top(m: Message) -> None:
    rows = db.get_top()
    if not rows:
        await m.answer("Рейтинг пока пуст — никто не закончил ни одного задания.")
        return
    lines = ["🏆 <b>Рейтинг</b>\n"]
    for i, r in enumerate(rows, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        lines.append(f"{medal} {html.escape(r['name'])} — {r['points']} б. ({r['done']} зад.)")
    await m.answer("\n".join(lines))


# ---------- текстовые ответы ----------

@router.message(F.text)
async def on_text(m: Message) -> None:
    sess = ACTIVE.get(m.from_user.id)
    if sess is None:
        await m.answer(
            "Сейчас активного задания нет — бот напишет сам в случайное время "
            "между 10:00 и 15:00 🙂 (или отправь /now для тренировки)"
        )
        return
    db.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    if sess["stage"] == "forms":
        await handle_forms(m, sess)
    else:
        await handle_sentences(m, sess)


@router.message()
async def on_other(m: Message) -> None:
    await m.answer("Пришли ответ текстом 🙂")


# ---------- расписание ----------

def random_times_today() -> list[dt.datetime]:
    """TASKS_PER_DAY случайных моментов сегодня внутри окна 10:00–15:00."""
    tz = tzinfo()
    start = now_tz().replace(
        hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0,
    )
    end = now_tz().replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    now = now_tz()
    if now >= end:  # окно уже прошло
        return []
    start = max(start, now + dt.timedelta(minutes=2))
    span = int((end - start).total_seconds())
    times: set[dt.datetime] = set()
    while len(times) < min(TASKS_PER_DAY, max(1, span // 1200)):
        times.add(start + dt.timedelta(seconds=random.randrange(span)))
    return sorted(times)


async def schedule_day_tasks(bot: Bot) -> None:
    """Планирует задания на сегодня; времена хранятся в БД, чтобы рестарт ничего не задублировал."""
    key = f"times_{now_tz().date().isoformat()}"
    stored = db.kv_get(key)
    if stored is not None:
        times = [dt.datetime.fromisoformat(s) for s in json.loads(stored)]
    else:
        times = random_times_today()
        db.kv_set(key, json.dumps([t.isoformat() for t in times]))

    for t in times:
        if t <= now_tz():
            continue
        scheduler.add_job(
            send_task_to_everyone, DateTrigger(run_date=t), args=[bot],
            id=f"tasks_{t.isoformat()}",
        )
        log.info("Scheduled tasks at %s", t.isoformat())


async def send_task_to_everyone(bot: Bot) -> None:
    for user_id in db.all_users():
        if user_id in ACTIVE:  # не прерываем незаконченное задание
            continue
        try:
            await send_task(bot, user_id)
        except Exception as exc:
            log.warning("Can't send task to a user: %s", exc)


# ---------- запуск ----------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")
    db.init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    # каждый день в 00:04 планируем случайные времена на наступивший день
    scheduler.add_job(
        schedule_day_tasks, CronTrigger(hour=0, minute=4), args=[bot], id="daily_plan",
    )
    await schedule_day_tasks(bot)  # и на случай запуска уже сегодня днём
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot started")
    polling = asyncio.create_task(dp.start_polling(bot))
    if MAX_RUN_MINUTES:  # режим GitHub Actions: гасим себя по расписанию
        await asyncio.sleep(MAX_RUN_MINUTES * 60)
        log.info("MAX_RUN_MINUTES=%s reached, stopping", MAX_RUN_MINUTES)
        polling.cancel()
    try:
        await polling
    except asyncio.CancelledError:
        pass
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
