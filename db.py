"""SQLite-хранилище: пользователи, сессии с баллами, служебные настройки расписания."""
import datetime as dt
import sqlite3

from config import DB_PATH

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    c = get_conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER,
            verb             TEXT,
            sent_at          REAL,
            forms_answer     TEXT,
            forms_sec        REAL,
            forms_ok         INTEGER,
            forms_points     INTEGER,
            sentences_answer TEXT,
            sentences_ok     INTEGER,
            sentences_points INTEGER,
            feedback         TEXT,
            total            INTEGER,
            ai_used          INTEGER DEFAULT 0,
            finished         INTEGER DEFAULT 0,
            created_at       TEXT,
            finished_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    c.commit()


# --- пользователи ---

def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    c = get_conn()
    c.execute(
        "INSERT INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET username = COALESCE(?, username), "
        "first_name = COALESCE(?, first_name)",
        (user_id, username, first_name, dt.datetime.now().isoformat(timespec="seconds"),
         username, first_name),
    )
    c.commit()


def all_users() -> list[int]:
    rows = get_conn().execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]


# --- сессии ---

def start_session(user_id: int, verb: str, sent_at: float) -> int:
    c = get_conn()
    cur = c.execute(
        "INSERT INTO sessions (user_id, verb, sent_at, created_at) VALUES (?, ?, ?, ?)",
        (user_id, verb, sent_at, dt.datetime.now().isoformat(timespec="seconds")),
    )
    c.commit()
    return int(cur.lastrowid)


def save_forms(session_id: int, answer: str, sec: float, ok: bool, points: int) -> None:
    c = get_conn()
    c.execute(
        "UPDATE sessions SET forms_answer = ?, forms_sec = ?, forms_ok = ?, forms_points = ? "
        "WHERE id = ?",
        (answer, round(sec, 2), int(ok), points, session_id),
    )
    c.commit()


def save_sentences(
    session_id: int, answer: str, ok: bool, points: int, feedback: str, total: int,
    ai_used: bool,
) -> None:
    c = get_conn()
    c.execute(
        "UPDATE sessions SET sentences_answer = ?, sentences_ok = ?, sentences_points = ?, "
        "feedback = ?, total = ?, finished = 1, finished_at = ?, ai_used = ? WHERE id = ?",
        (answer, int(ok), points, feedback, total,
         dt.datetime.now().isoformat(timespec="seconds"), int(ai_used), session_id),
    )
    c.commit()


def mark_skipped(session_id: int) -> None:
    c = get_conn()
    c.execute(
        "UPDATE sessions SET finished = 1, total = COALESCE(total, 0), "
        "finished_at = ? WHERE id = ?",
        (dt.datetime.now().isoformat(timespec="seconds"), session_id),
    )
    c.commit()


def get_stats(user_id: int) -> dict:
    c = get_conn()
    r = c.execute(
        """
        SELECT COUNT(*)                       AS done,
               COALESCE(SUM(total), 0)        AS points,
               COALESCE(SUM(forms_ok), 0)     AS forms_ok,
               COALESCE(SUM(sentences_ok), 0) AS sent_ok,
               COALESCE(AVG(CASE WHEN forms_ok = 1 THEN forms_sec END), 0) AS avg_sec,
               COALESCE(MIN(CASE WHEN forms_ok = 1 THEN forms_sec END), 0) AS best_sec
        FROM sessions WHERE user_id = ? AND finished = 1
        """,
        (user_id,),
    ).fetchone()
    return dict(r)


def get_top(limit: int = 10) -> list[dict]:
    rows = get_conn().execute(
        """
        SELECT s.user_id,
               COALESCE(u.first_name, u.username, 'ученик ' || s.user_id) AS name,
               SUM(s.total) AS points,
               COUNT(*)     AS done
        FROM sessions s LEFT JOIN users u ON u.user_id = s.user_id
        WHERE s.finished = 1
        GROUP BY s.user_id
        ORDER BY points DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_user_data(user_id: int) -> int:
    """Полное удаление данных пользователя (команда /forget). Возвращает число записей."""
    c = get_conn()
    cur = c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    deleted = cur.rowcount
    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    c.commit()
    return max(deleted, 0)


# --- служебное хранилище расписания (key/value) ---

def kv_get(key: str, default: str | None = None) -> str | None:
    row = get_conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    c = get_conn()
    c.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    c.commit()
