"""
Telethon Listener — Iteracja 3: Read, Download, Forward & AI Parse.

Co robi:
  1. Loguje się do Telegrama jako Ty (userbot)
  2. Nasłuchuje nowych wiadomości w SOURCE_GROUP_ID
  3. Pobiera media (zdjęcia, dokumenty) i zapisuje lokalnie
  4. Zapisuje każdą wiadomość do SQLite (z ścieżkami do mediów)
  5. Forwarduje wiadomość do RAW_CHANNEL_ID
  6. Analizuje wiadomość przez Google Gemini (AI parser)

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
from src.storage import init_db, save_raw_message, update_media_paths, save_ai_analysis, count_messages, save_trader_positions
from src.parser import analyze_message
from src.notifier import send_signal_notification


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

    # --- 4. Analiza AI (jeśli klucz ustawiony) ---
    if settings.gemini_api_key:
        try:
            ai_result = await analyze_message(
                text=msg.text or None,
                media_paths=media_paths if media_paths else None,
            )
            # Dołącz źródło (IKE/IKZE) jeśli wiadomość pochodzi z grupy Damiana
            source_topic = _damian_topic_map.pop(msg.id, None)
            if source_topic:
                ai_result["source_topic"] = source_topic
                logger.info(f"🏷  Źródło: {source_topic}")

            save_ai_analysis(msg.id, event.chat_id, ai_result)

            # Zapisz strukturalne pozycje portfela tradera
            if ai_result.get("message_type") == "PORTFOLIO_UPDATE":
                positions = ai_result.get("portfolio_positions")
                if positions and isinstance(positions, list) and len(positions) > 0:
                    save_trader_positions(msg.id, positions)

            logger.info(
                f"🤖 AI: {ai_result.get('message_type', '?')} | "
                f"confidence={ai_result.get('confidence', 0):.2f} | "
                f"{ai_result.get('summary', '?')}"
            )

            # --- 6. Wyślij powiadomienie docelowe (sygnał lub portfel) ---
            msg_type   = ai_result.get("message_type")
            confidence = ai_result.get("confidence", 0.0)
            if msg_type in ("TRADE_ACTION", "PORTFOLIO_UPDATE") and confidence >= 0.6:
                await send_signal_notification(msg.id, ai_result, media_paths)
            else:
                logger.debug(
                    f"ℹ️ Bez powiadomienia: type={msg_type}, "
                    f"confidence={confidence:.2f} (próg 0.6)"
                )

        except Exception as e:
            logger.error(f"❌ Błąd AI analizy: {e}")


# ============================================================
# Heartbeat (monitor bot sprawdza ten plik)
# ============================================================

HEARTBEAT_FILE = LOGS_DIR.parent / ".heartbeat"

_last_message_at: str = "brak"
_start_time = None
_damian_topic_map: dict[int, str] = {}  # forwarded_msg_id → "IKE" / "IKZE"


def write_heartbeat() -> None:
    """Zapisuje heartbeat do pliku JSON."""
    import json
    from datetime import timedelta
    import time

    global _start_time
    if _start_time is None:
        _start_time = time.time()

    uptime_delta = timedelta(seconds=int(time.time() - _start_time))
    days = uptime_delta.days
    hours, remainder = divmod(uptime_delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    data = {
        "timestamp": datetime.utcnow().isoformat(),
        "uptime": f"{days}d {hours}h {minutes}m",
        "last_message_at": _last_message_at,
        "messages_total": count_messages(),
    }
    HEARTBEAT_FILE.write_text(json.dumps(data))


async def heartbeat_loop() -> None:
    """Co 5 minut zapisuje heartbeat."""
    while True:
        try:
            write_heartbeat()
            logger.debug("💓 Heartbeat zapisany")
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
        await asyncio.sleep(300)  # 5 minut


# ============================================================
# Główna pętla
# ============================================================

async def main() -> None:
    global _last_message_at, _start_time
    import time
    _start_time = time.time()

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

    # Handler 1 — wiadomości z test-bot-inwestor (główny pipeline AI)
    source_filter = events.NewMessage(chats=settings.source_group_id if settings.source_group_id else None)

    @client.on(source_filter)
    async def _handler(event: events.NewMessage.Event) -> None:
        global _last_message_at
        _last_message_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        await handle_new_message(event, client)

    # Handler 2 — wiadomości z prywatnej grupy Damiana (IKE/IKZE → forward live)
    # Handler 3 — komendy /fetch z kanału recive-bot-investor
    if settings.damian_group_id:
        from src.damian_watcher import (
            is_watched_topic, get_topic_id, TOPIC_NAMES,
            parse_fetch_command, fetch_and_forward,
        )
        import httpx as _httpx

        async def _bot_reply(text: str) -> None:
            """Wysyła wiadomość na recive-bot-investor przez Bot API."""
            async with _httpx.AsyncClient(timeout=10.0) as h:
                await h.post(
                    f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                    json={"chat_id": settings.raw_channel_id, "text": text, "parse_mode": "Markdown"},
                )

        @client.on(events.NewMessage(chats=settings.damian_group_id))
        async def _damian_handler(event: events.NewMessage.Event) -> None:
            msg = event.message
            if not is_watched_topic(msg):
                return
            topic_name = TOPIC_NAMES.get(get_topic_id(msg), "?")
            try:
                fwd = await client.forward_messages(
                    entity=settings.source_group_id,
                    messages=msg.id,
                    from_peer=settings.damian_group_id,
                )
                if fwd:
                    fwd_id = (fwd[0] if isinstance(fwd, list) else fwd).id
                    _damian_topic_map[fwd_id] = topic_name
                logger.info(f"📩 Damian [{topic_name}] msg {msg.id} → test-bot-inwestor")
            except Exception as e:
                logger.error(f"❌ Forward Damian [{topic_name}] msg {msg.id}: {e}")

        @client.on(events.NewMessage(chats=settings.raw_channel_id))
        async def _fetch_handler(event: events.NewMessage.Event) -> None:
            text = (event.message.text or "").strip()
            topic_id, count = parse_fetch_command(text)
            if not topic_id:
                return

            topic_name = TOPIC_NAMES[topic_id]
            logger.info(f"📋 /fetch [{topic_name}] x{count} — zaczynam")
            await _bot_reply(f"⏳ Pobieram *{count}* wiadomości z *{topic_name}*...")

            try:
                forwarded, topic_map = await fetch_and_forward(client, topic_id, count)
                _damian_topic_map.update(topic_map)
                if forwarded > 0:
                    await _bot_reply(
                        f"✅ Przesłano *{forwarded}/{count}* wiadomości z *{topic_name}* → test-bot-inwestor\n"
                        f"_AI przeanalizuje i wyśle wyniki tutaj._"
                    )
                else:
                    await _bot_reply(
                        f"⚠️ Pobrano *0* wiadomości z *{topic_name}*\n"
                        f"_Możliwe przyczyny: brak dostępu do grupy Damiana, "
                        f"zły topic ID ({topic_id}), lub temat jest pusty._"
                    )
            except Exception as e:
                logger.error(f"❌ fetch_and_forward błąd: {e}")
                await _bot_reply(f"❌ Błąd pobierania z *{topic_name}*:\n`{e}`")

        logger.info(f"   Damian:    {settings.damian_group_id} (IKE:{settings.damian_ike_topic_id} IKZE:{settings.damian_ikze_topic_id})")
        logger.info(f"   /fetch:    nasłuchuję na {settings.raw_channel_id}")

    async with client:
        me = await client.get_me()
        logger.info(f"✅ Zalogowano jako: {me.first_name} (@{me.username})")
        logger.info("👂 Nasłuchuję... (Ctrl+C żeby zatrzymać)")

        # Pierwszy heartbeat
        write_heartbeat()

        # Uruchom heartbeat w tle
        asyncio.create_task(heartbeat_loop())

        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
