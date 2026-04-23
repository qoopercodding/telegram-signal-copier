"""
Telethon Listener — Iteracja 2: Read, Download Media & Forward.

Co robi:
  1. Loguje się do Telegrama jako Ty (userbot)
  2. Nasłuchuje nowych wiadomości w SOURCE_GROUP_ID
  3. Pobiera media (zdjęcia, dokumenty) i zapisuje lokalnie
  4. Zapisuje każdą wiadomość do SQLite (z ścieżkami do mediów)
  5. Forwarduje wiadomość do RAW_CHANNEL_ID

Uruchomienie:
    python -m src.listener

Pierwsze uruchomienie: zapyta o numer telefonu + kod SMS → tworzy plik .session
"""

import asyncio
from datetime import datetime
from pathlib import Path

from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import (
    Message,
    MessageMediaPhoto,
    MessageMediaDocument,
)

from src.config import settings, ensure_directories, LOGS_DIR, MEDIA_DIR
from src.storage import init_db, save_raw_message, mark_forwarded, update_media_paths, count_messages


# ============================================================
# Konfiguracja logowania
# ============================================================

def setup_logging() -> None:
    """Konfiguruje loguru — logi do konsoli i do pliku."""
    log_file = LOGS_DIR / "listener_{time:YYYY-MM-DD}.log"
    logger.add(
        str(log_file),
        rotation="1 day",
        retention="7 days",
        level="DEBUG",
        encoding="utf-8",
    )


# ============================================================
# Klient Telethon
# ============================================================

def build_client() -> TelegramClient:
    """Tworzy klienta Telethon z konfiguracją z .env"""
    session_path = str(LOGS_DIR.parent / settings.session_name)
    return TelegramClient(
        session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


# ============================================================
# Pobieranie mediów
# ============================================================

async def download_media(msg: Message, client: TelegramClient) -> list[str]:
    """
    Pobiera media z wiadomości i zapisuje do media/.
    Zwraca listę ścieżek do pobranych plików.
    """
    if not msg.media:
        return []

    media_paths: list[str] = []

    # Generuj nazwę pliku: YYYYMMDD_HHMMSS_msgID
    ts = msg.date.strftime("%Y%m%d_%H%M%S") if msg.date else "unknown"
    base_name = f"{ts}_{msg.id}"

    try:
        if isinstance(msg.media, MessageMediaPhoto):
            # --- Zdjęcie ---
            file_path = MEDIA_DIR / f"{base_name}.jpg"
            await client.download_media(msg, file=str(file_path))
            media_paths.append(str(file_path))
            logger.info(f"📷 Pobrano zdjęcie → {file_path.name}")

        elif isinstance(msg.media, MessageMediaDocument):
            # --- Dokument (PDF, screenshot PNG, itp.) ---
            doc = msg.media.document
            # Odczytaj oryginalną nazwę pliku jeśli jest
            ext = ".bin"
            if doc and doc.mime_type:
                mime_to_ext = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                    "application/pdf": ".pdf",
                    "video/mp4": ".mp4",
                }
                ext = mime_to_ext.get(doc.mime_type, ".bin")

            file_path = MEDIA_DIR / f"{base_name}{ext}"
            await client.download_media(msg, file=str(file_path))
            media_paths.append(str(file_path))
            logger.info(f"📎 Pobrano dokument → {file_path.name} ({doc.mime_type})")

        else:
            logger.debug(f"⏭️ Pominięto media typu: {type(msg.media).__name__}")

    except Exception as e:
        logger.error(f"❌ Błąd pobierania mediów z msg {msg.id}: {e}")

    return media_paths


# ============================================================
# Handler nowych wiadomości
# ============================================================

async def handle_new_message(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """
    Wywoływany przy każdej nowej wiadomości w SOURCE_GROUP_ID.
    Pobiera media, zapisuje do SQLite i forwarduje do RAW_CHANNEL_ID.
    """
    msg: Message = event.message

    # --- 1. Sprawdź czy źródło się zgadza ---
    source_id = settings.source_group_id
    if source_id != 0 and event.chat_id != source_id:
        return  # Ignoruj wiadomości z innych czatów

    logger.info(
        f"📨 Nowa wiadomość | id={msg.id} | chat={event.chat_id} | "
        f"media={'📷' if msg.media else '❌'} | "
        f"tekst={repr(msg.text[:60]) if msg.text else '(brak tekstu)'}"
    )

    # --- 2. Zapisz do SQLite (na początku bez mediów) ---
    saved = save_raw_message(
        message_id=msg.id,
        chat_id=event.chat_id,
        timestamp=msg.date if isinstance(msg.date, datetime) else datetime.utcnow(),
        raw_text=msg.text or None,
        has_media=bool(msg.media),
        media_paths=[],
        grouped_id=msg.grouped_id,
    )

    if not saved:
        logger.warning(f"Duplikat wiadomości {msg.id} — pomijam")
        return

    # --- 3. Pobierz media (jeśli są) ---
    media_paths = await download_media(msg, client)
    if media_paths:
        update_media_paths(msg.id, event.chat_id, media_paths)
        logger.success(f"💾 Zapisano {len(media_paths)} plik(ów) dla msg {msg.id}")

    # --- 4. Forwarduj do kanału docelowego ---
    target_id = settings.raw_channel_id
    if target_id == 0:
        logger.warning("RAW_CHANNEL_ID nie ustawione — pomijam forward (tylko zapis do SQLite)")
        return

    try:
        forwarded = await client.forward_messages(
            entity=target_id,
            messages=msg.id,
            from_peer=event.chat_id,
        )
        # Telethon zwraca listę lub pojedynczy obiekt
        fwd_id = forwarded[0].id if isinstance(forwarded, list) else forwarded.id
        mark_forwarded(msg.id, event.chat_id, fwd_id)
        logger.success(f"✅ Forward {msg.id} → {target_id} (nowe ID: {fwd_id})")

    except Exception as e:
        logger.error(f"❌ Błąd forwardu wiadomości {msg.id}: {e}")


# ============================================================
# Główna pętla
# ============================================================

async def main() -> None:
    ensure_directories()
    setup_logging()
    init_db()

    logger.info("🚀 Telegram Signal Copier — Iteracja 2")
    logger.info(f"   Źródło:    {settings.source_group_id or '(wszystkie czaty — ustaw SOURCE_GROUP_ID)'}")
    logger.info(f"   Cel:       {settings.raw_channel_id or '(brak — ustaw RAW_CHANNEL_ID)'}")
    logger.info(f"   Media:     {MEDIA_DIR}")
    logger.info(f"   Baza:      {settings.db_path}")
    logger.info(f"   Wiadomości w bazie: {count_messages()}")

    client = build_client()

    # Rejestruj handler — filtr po chat_id jeśli ustawiony
    source_filter = events.NewMessage(chats=settings.source_group_id if settings.source_group_id else None)

    @client.on(source_filter)
    async def _handler(event: events.NewMessage.Event) -> None:
        await handle_new_message(event, client)

    async with client:
        me = await client.get_me()
        logger.info(f"✅ Zalogowano jako: {me.first_name} (@{me.username})")
        logger.info("👂 Nasłuchuję... (Ctrl+C żeby zatrzymać)")
        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
