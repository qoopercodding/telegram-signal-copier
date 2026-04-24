"""
Damian Watcher — Telethon userbot nasłuchujący tematów IKE/IKZE
z prywatnej grupy Damiana i forwardujący wiadomości do test-bot-inwestor.

Funkcje:
  - Live watch: każda nowa wiadomość z IKE/IKZE → forward do test-bot-inwestor
  - Fetch history: komenda z recive-bot-investor pobiera N ostatnich wiadomości

Uruchomienie:
    python -m src.damian_watcher

Komendy (z kanału recive-bot-investor):
    /fetch IKE 22          — ostatnie 22 posty z tematu IKE
    /fetch IKZE 15         — ostatnie 15 postów z tematu IKZE
    weź 10 ostatnich z IKE — ten sam efekt (wolny tekst)

WAŻNE: używa osobnej sesji (damian_watcher.session) — nie koliduje z listener.py
"""

import asyncio
import re
from datetime import datetime

from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import Message

from src.config import settings, ensure_directories, LOGS_DIR


# ── Stałe ──────────────────────────────────────────────────────────────────

DAMIAN_GROUP_ID = settings.damian_group_id        # -1001548727545
IKE_TOPIC_ID    = settings.damian_ike_topic_id    # 8951
IKZE_TOPIC_ID   = settings.damian_ikze_topic_id   # 8953
STAGING_CHANNEL = settings.source_group_id        # test-bot-inwestor
OUTPUT_CHANNEL  = settings.raw_channel_id         # recive-bot-investor (komendy)

TOPIC_NAMES = {
    IKE_TOPIC_ID:  "IKE",
    IKZE_TOPIC_ID: "IKZE",
}

# Regex do parsowania komend fetch
_FETCH_RE = re.compile(
    r'(?:/fetch\s+)?'
    r'(ike|ikze)'
    r'[\s,]+(\d+)',
    re.IGNORECASE,
)
_FETCH_RE2 = re.compile(
    r'we[zź]\s+(\d+)\s+\w+\s+z\s+(ike|ikze)',
    re.IGNORECASE,
)


# ── Klient Telethon ─────────────────────────────────────────────────────────

def build_client() -> TelegramClient:
    """Osobna sesja (damian_watcher.session) — nie koliduje z listener.py."""
    session_path = str(LOGS_DIR.parent / settings.damian_session_name)
    return TelegramClient(
        session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_topic_id(msg: Message) -> int | None:
    """
    Zwraca ID tematu (top_id) jeśli wiadomość należy do forum topic.
    reply_to_top_id  — dla głębszych odpowiedzi
    reply_to_msg_id  — dla bezpośredniej odpowiedzi na header tematu
    """
    if not msg.reply_to:
        return None
    top    = getattr(msg.reply_to, "reply_to_top_id", None)
    msg_id = getattr(msg.reply_to, "reply_to_msg_id", None)
    return top or msg_id


def is_watched_topic(msg: Message) -> bool:
    """True jeśli wiadomość pochodzi z tematu IKE lub IKZE."""
    tid = get_topic_id(msg)
    return tid in (IKE_TOPIC_ID, IKZE_TOPIC_ID)


def parse_fetch_command(text: str) -> tuple[int | None, int]:
    """
    Parsuje komendę fetch z tekstu.
    Zwraca (topic_id, count) lub (None, 0).

    Obsługiwane formaty:
        /fetch IKE 22
        /fetch IKZE 15
        weź 10 ostatnich z IKE
        ike 22
    """
    m = _FETCH_RE.search(text)
    if m:
        name     = m.group(1).upper()
        count    = min(int(m.group(2)), 50)
        topic_id = IKE_TOPIC_ID if name == "IKE" else IKZE_TOPIC_ID
        return topic_id, count

    m = _FETCH_RE2.search(text)
    if m:
        count    = min(int(m.group(1)), 50)
        name     = m.group(2).upper()
        topic_id = IKE_TOPIC_ID if name == "IKE" else IKZE_TOPIC_ID
        return topic_id, count

    return None, 0


# ── Handlers ─────────────────────────────────────────────────────────────────

async def handle_new_message(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """
    Wywoływany dla każdej nowej wiadomości w grupie Damiana.
    Forwarduje do test-bot-inwestor jeśli pochodzi z IKE lub IKZE.
    """
    msg: Message = event.message

    if not is_watched_topic(msg):
        return

    topic_id   = get_topic_id(msg)
    topic_name = TOPIC_NAMES.get(topic_id, str(topic_id))

    logger.info(
        f"📩 [{topic_name}] Nowa wiadomość id={msg.id} | "
        f"media={'📷' if msg.media else '❌'} | "
        f"tekst={repr(msg.text[:60]) if msg.text else '(brak)'}"
    )

    try:
        await client.forward_messages(
            entity=STAGING_CHANNEL,
            messages=msg.id,
            from_peer=DAMIAN_GROUP_ID,
        )
        logger.success(f"✅ Forward [{topic_name}] msg {msg.id} → test-bot-inwestor")
    except Exception as e:
        logger.error(f"❌ Błąd forwardu [{topic_name}] msg {msg.id}: {e}")


async def fetch_and_forward(
    client: TelegramClient,
    topic_id: int,
    count: int,
) -> int:
    """
    Pobiera ostatnie `count` wiadomości z tematu i forwarduje do test-bot-inwestor.
    Zwraca liczbę faktycznie przesłanych wiadomości.
    """
    topic_name = TOPIC_NAMES.get(topic_id, str(topic_id))
    logger.info(f"📥 Pobieranie ostatnich {count} wiadomości z [{topic_name}]...")

    message_ids: list[int] = []

    async for msg in client.iter_messages(
        entity=DAMIAN_GROUP_ID,
        reply_to=topic_id,
        limit=count,
    ):
        message_ids.append(msg.id)

    if not message_ids:
        logger.warning(f"Brak wiadomości w [{topic_name}]")
        return 0

    message_ids.reverse()  # forward w kolejności chronologicznej

    forwarded = 0
    for msg_id in message_ids:
        try:
            await client.forward_messages(
                entity=STAGING_CHANNEL,
                messages=msg_id,
                from_peer=DAMIAN_GROUP_ID,
            )
            forwarded += 1
            await asyncio.sleep(0.3)  # throttle — Telegram rate limit
        except Exception as e:
            logger.error(f"❌ Błąd forwardu msg {msg_id}: {e}")

    logger.success(
        f"✅ Fetch [{topic_name}]: {forwarded}/{len(message_ids)} wiadomości → test-bot-inwestor"
    )
    return forwarded


async def handle_user_command(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """
    Nasłuchuje komend na recive-bot-investor.
    Trigger: /fetch IKE 22  lub  weź 10 ostatnich z IKZE
    """
    text = (event.message.text or "").strip()
    if not text:
        return

    topic_id, count = parse_fetch_command(text)
    if not topic_id or count <= 0:
        return

    topic_name = TOPIC_NAMES[topic_id]
    logger.info(f"📋 Komenda fetch: [{topic_name}] x{count}")

    try:
        await client.send_message(
            OUTPUT_CHANNEL,
            f"⏳ Pobieram ostatnie **{count}** wiadomości z tematu **{topic_name}**...",
        )
    except Exception:
        pass

    forwarded = await fetch_and_forward(client, topic_id, count)

    try:
        await client.send_message(
            OUTPUT_CHANNEL,
            f"✅ Przesłano **{forwarded}** wiadomości z [{topic_name}] → test-bot-inwestor.\n"
            f"Bot przetworzy je i wyśle analizę tutaj.",
        )
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    ensure_directories()

    log_file = LOGS_DIR / "damian_watcher_{time:YYYY-MM-DD}.log"
    logger.add(
        str(log_file),
        rotation="1 day",
        retention="7 days",
        level="DEBUG",
        encoding="utf-8",
    )

    if not DAMIAN_GROUP_ID:
        logger.error("DAMIAN_GROUP_ID nie ustawiony w .env — kończę")
        return

    client = build_client()

    @client.on(events.NewMessage(chats=DAMIAN_GROUP_ID))
    async def _on_damian(event):
        await handle_new_message(event, client)

    if OUTPUT_CHANNEL:
        @client.on(events.NewMessage(chats=OUTPUT_CHANNEL))
        async def _on_command(event):
            await handle_user_command(event, client)

    async with client:
        me = await client.get_me()
        logger.info(f"🚀 Damian Watcher uruchomiony jako: {me.first_name} (@{me.username})")
        logger.info(f"   Grupa Damiana: {DAMIAN_GROUP_ID}")
        logger.info(f"   IKE topic:     {IKE_TOPIC_ID} | IKZE topic: {IKZE_TOPIC_ID}")
        logger.info(f"   Staging:       {STAGING_CHANNEL} (test-bot-inwestor)")
        logger.info(f"   Komendy z:     {OUTPUT_CHANNEL} (recive-bot-investor)")
        logger.info("👂 Nasłuchuję... (Ctrl+C żeby zatrzymać)")
        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
