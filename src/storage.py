"""
Warstwa przechowywania danych — SQLite.

Tworzy bazę danych i udostępnia funkcje do zapisu/odczytu wiadomości.
Iteracja 1: tylko tabela raw_messages (proste kopiowanie bez AI).
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.config import settings, DB_DIR


# ============================================================
# Inicjalizacja bazy
# ============================================================

CREATE_RAW_MESSAGES = """
CREATE TABLE IF NOT EXISTS raw_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,           -- Telegram message ID (ze źródła)
    chat_id         INTEGER NOT NULL,           -- ID kanału źródłowego
    timestamp       TEXT NOT NULL,              -- ISO 8601
    raw_text        TEXT,                       -- Treść wiadomości (może być NULL dla samych mediów)
    has_media       INTEGER NOT NULL DEFAULT 0, -- 1 = zawiera media
    media_paths     TEXT NOT NULL DEFAULT '',   -- JSON lista ścieżek do plików lokalnych
    grouped_id      INTEGER,                    -- ID media_group (kilka zdjęć = jedna wiadomość)
    forwarded_to    INTEGER,                    -- ID wiadomości w kanale docelowym (po forwardzie)
    created_at      TEXT NOT NULL,              -- Kiedy zapisano do bazy
    UNIQUE(message_id, chat_id)                 -- Nie zapisuj dwa razy tej samej wiadomości
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_timestamp ON raw_messages(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_grouped_id ON raw_messages(grouped_id);",
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_forwarded_to ON raw_messages(forwarded_to);",
]


def get_connection() -> sqlite3.Connection:
    """Zwraca połączenie z bazą SQLite. Tworzy plik jeśli nie istnieje."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row   # Wyniki jako dict-like obiekty
    conn.execute("PRAGMA journal_mode=WAL;")  # Lepsze współbieżne zapisy
    return conn


def init_db() -> None:
    """Tworzy tabele i indeksy jeśli nie istnieją."""
    with get_connection() as conn:
        conn.execute(CREATE_RAW_MESSAGES)
        for idx_sql in CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.commit()
    logger.info(f"SQLite zainicjalizowany: {settings.db_path}")


# ============================================================
# Operacje na wiadomościach
# ============================================================

def save_raw_message(
    message_id: int,
    chat_id: int,
    timestamp: datetime,
    raw_text: str | None,
    has_media: bool = False,
    media_paths: list[str] | None = None,
    grouped_id: int | None = None,
) -> bool:
    """
    Zapisuje surową wiadomość do bazy.

    Returns:
        True  — nowy rekord zapisany
        False — duplikat (już istnieje)
    """
    import json

    media_json = json.dumps(media_paths or [])
    now = datetime.utcnow().isoformat()

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO raw_messages
                    (message_id, chat_id, timestamp, raw_text, has_media, media_paths, grouped_id, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    chat_id,
                    timestamp.isoformat(),
                    raw_text,
                    int(has_media),
                    media_json,
                    grouped_id,
                    now,
                ),
            )
            conn.commit()
        logger.debug(f"Zapisano wiadomość {message_id} z chat {chat_id}")
        return True

    except sqlite3.IntegrityError:
        logger.debug(f"Duplikat — wiadomość {message_id} już istnieje, pomijam")
        return False


def mark_forwarded(message_id: int, chat_id: int, forwarded_to: int) -> None:
    """Zapisuje ID wiadomości w kanale docelowym po forwardzie."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE raw_messages SET forwarded_to = ? WHERE message_id = ? AND chat_id = ?",
            (forwarded_to, message_id, chat_id),
        )
        conn.commit()
    logger.debug(f"Wiadomość {message_id} → forwarded_to={forwarded_to}")


def update_media_paths(message_id: int, chat_id: int, media_paths: list[str]) -> None:
    """Aktualizuje ścieżki do pobranych mediów w SQLite."""
    import json

    media_json = json.dumps(media_paths)
    with get_connection() as conn:
        conn.execute(
            "UPDATE raw_messages SET media_paths = ? WHERE message_id = ? AND chat_id = ?",
            (media_json, message_id, chat_id),
        )
        conn.commit()
    logger.debug(f"Wiadomość {message_id} → media_paths zaktualizowane ({len(media_paths)} plików)")


def count_messages() -> int:
    """Zwraca łączną liczbę zapisanych wiadomości."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM raw_messages").fetchone()
        return row["cnt"]


def get_recent_messages(limit: int = 10) -> list[sqlite3.Row]:
    """Zwraca ostatnie N wiadomości (do debugowania)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM raw_messages ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
