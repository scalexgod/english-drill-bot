"""Конфигурация бота: всё читается из .env (шаблон — .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# --- OpenRouter (OpenAI-совместимый API для проверки ответов) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
# Список моделей через запятую: если одна перегружена/молчит — пробуется следующая
OPENROUTER_MODELS = [
    m.strip()
    for m in os.getenv(
        "OPENROUTER_MODELS",
        "baidu/ernie-5.5-flash-preview:free,nvidia/nemotron-3.5-lightning:free,"
        "recursive/flint-v1.0:free,openrouter/free",
    ).split(",")
    if m.strip()
]

# --- База данных ---
DB_PATH = os.getenv("DB_PATH", "bot.db")

# --- Расписание: TASKS_PER_DAY случайных моментов внутри окна [START, END) ---
TASKS_PER_DAY = int(os.getenv("TASKS_PER_DAY", "2"))
WINDOW_START_HOUR = int(os.getenv("WINDOW_START_HOUR", "10"))
WINDOW_END_HOUR = int(os.getenv("WINDOW_END_HOUR", "15"))
TIMEZONE = os.getenv("TIMEZONE", "").strip()  # например "Europe/Moscow"; пусто = время компьютера

# Авто-остановка через N минут после старта (для GitHub Actions, чтобы job
# завершился сам и сохранил базу). 0 = работать бесконечно (локальный запуск)
MAX_RUN_MINUTES = int(os.getenv("MAX_RUN_MINUTES", "0") or "0")

# --- Баллы ---
FAST_LIMIT_SEC = 25   # ответил быстрее 25 секунд
FAST_POINTS = 25      # максимум за формы
MID_LIMIT_SEC = 60    # от 25 секунд до 1 минуты
MID_POINTS = 18
SLOW_POINTS = 10      # дольше минуты
SENTENCE_POINTS = 10  # бонус за хорошую часть с предложениями
MAX_SESSION_POINTS = FAST_POINTS + SENTENCE_POINTS  # 35
