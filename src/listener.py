"""
Telethon Listener — Iteracja 1: Read & Forward.

Co robi:
  1. Loguje się do Telegrama jako Ty (userbot)
  2. Nasłuchuje nowych wiadomości w SOURCE_GROUP_ID
  3. Zapisuje każdą wiadomość do SQLite
  4. Forwarduje wiadomość do RAW_CHANNEL_ID

Uruchomienie:
    python -m src.listener

Pierwsze uruchomienie: zapyta o numer telefonu + kod SMS → tworzy plik .session
"""

import asyncio
from datetime import datetime

from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import Message

from src.config import settings, ensure_directories, LOGS_DIR
from src.storage import init_db, save_raw_message, mark_forwarded, count_messages


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
# Handler nowych wiadomości
# ============================================================

async def handle_new_message(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """
    Wywoływany przy każdej nowej wiadomości w SOURCE_GROUP_ID.
    Zapisuje do SQLite i forwarduje do RAW_CHANNEL_ID.
    """
    msg: Message = event.message

    # --- 1. Sprawdź czy źródło się zgadza ---
    source_id = settings.source_group_id
    if source_id != 0 and event.chat_id != source_id:
        return  # Ignoruj wiadomości z innych czatów

    logger.info(
        f"📨 Nowa wiadomość | id={msg.id} | chat={event.chat_id} | "
        f"tekst={repr(msg.text[:60]) if msg.text else '(brak tekstu)'}"
    )

    # --- 2. Zapisz do SQLite ---
    saved = save_raw_message(
        message_id=msg.id,
        chat_id=event.chat_id,
        timestamp=msg.date if isinstance(msg.date, datetime) else datetime.utcnow(),
        raw_text=msg.text or None,
        has_media=bool(msg.media),
        media_paths=[],   # Iteracja 2: pobieranie mediów
        grouped_id=msg.grouped_id,
    )

    if not saved:
        logger.warning(f"Duplikat wiadomości {msg.id} — pomijam forward")
        return

    # --- 3. Forwarduj do kanału docelowego ---
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

    logger.info("🚀 Telegram Signal Copier — Iteracja 1")
    logger.info(f"   Źródło:    {settings.source_group_id or '(wszystkie czaty — ustaw SOURCE_GROUP_ID)'}")
    logger.info(f"   Cel:       {settings.raw_channel_id or '(brak — ustaw RAW_CHANNEL_ID)'}")
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
