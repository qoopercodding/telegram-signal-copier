"""
Damian Watcher — Telethon userbot nasłuchujący tematów IKE/IKZE
z prywatnej grupy Damiana i forwardujący wiadomości do test-bot-inwestor.

Funkcje:
  - Live watch: każda nowa wiadomość z IKE/IKZE → forward do test-bot-inwestor
  - Fetch history: komenda z recive-bot-investor pobiera N ostatnich wiadomości
  - Logowanie przez Telegram: kod SMS wpisujesz na kanale recive-bot-investor

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
import time

import httpx
from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import Message

from src.config import settings, ensure_directories, LOGS_DIR


# ── Stałe ──────────────────────────────────────────────────────────────────

DAMIAN_GROUP_ID = settings.damian_group_id        # -1001548727545
IKE_TOPIC_ID    = settings.damian_ike_topic_id    # 8951
IKZE_TOPIC_ID   = settings.damian_ikze_topic_id   # 8953
STAGING_CHANNEL = settings.source_group_id        # test-bot-inwestor
OUTPUT_CHANNEL  = settings.raw_channel_id         # recive-bot-investor

TOPIC_NAMES = {
    IKE_TOPIC_ID:  "IKE",
    IKZE_TOPIC_ID: "IKZE",
}

BOT_API = f"https://api.telegram.org/bot{settings.bot_token}"

_FETCH_RE = re.compile(r'(?:/fetch\s+)?(ike|ikze)[\s,]+(\d+)', re.IGNORECASE)
_FETCH_RE2 = re.compile(r'we[zź]\s+(\d+)\s+\w+\s+z\s+(ike|ikze)', re.IGNORECASE)


# ── Klient Telethon ─────────────────────────────────────────────────────────

def build_client() -> TelegramClient:
    """Osobna sesja (damian_watcher.session) — nie koliduje z listener.py."""
    session_path = str(LOGS_DIR.parent / settings.damian_session_name)
    return TelegramClient(
        session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


# ── Logowanie przez kanał Telegram ──────────────────────────────────────────

async def _bot_send(text: str) -> None:
    """Wysyła wiadomość na recive-bot-investor przez Bot API."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        await http.post(f"{BOT_API}/sendMessage", json={
            "chat_id": OUTPUT_CHANNEL,
            "text": text,
            "parse_mode": "Markdown",
        })


async def _get_current_offset() -> int:
    """Pobiera aktualny offset żeby ignorować stare wiadomości."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.get(f"{BOT_API}/getUpdates", params={"limit": 1, "offset": -1})
        updates = resp.json().get("result", [])
        if updates:
            return updates[-1]["update_id"] + 1
    return 0


async def _poll_for_reply(offset: int, pattern: str, timeout: int = 120) -> str:
    """
    Czeka na wiadomość pasującą do `pattern` na kanale recive-bot-investor.
    Używa long-polling Bot API getUpdates.
    """
    deadline = time.time() + timeout
    current_offset = offset

    async with httpx.AsyncClient(timeout=35.0) as http:
        while time.time() < deadline:
            remaining = int(deadline - time.time())
            if remaining <= 0:
                break

            resp = await http.get(f"{BOT_API}/getUpdates", params={
                "offset": current_offset,
                "timeout": min(20, remaining),
                "allowed_updates": ["channel_post", "message"],
            })

            if resp.status_code != 200:
                await asyncio.sleep(1)
                continue

            for update in resp.json().get("result", []):
                current_offset = update["update_id"] + 1
                post = update.get("channel_post") or update.get("message")
                if not post:
                    continue
                chat_id = post.get("chat", {}).get("id")
                if chat_id != OUTPUT_CHANNEL:
                    continue
                text = (post.get("text") or "").strip()
                if re.match(pattern, text):
                    return text

    raise TimeoutError(f"Brak odpowiedzi w ciągu {timeout}s")


async def _ask_for_sms_code() -> str:
    """Wysyła prośbę o kod SMS na kanał i czeka na odpowiedź (2 min)."""
    offset = await _get_current_offset()
    await _bot_send(
        "🔑 *damian\\_watcher* loguje się do Telegrama.\n\n"
        "Wpisz tutaj *5-cyfrowy kod SMS* który właśnie dostałeś:"
    )
    logger.info("⏳ Czekam na kod SMS na kanale recive-bot-investor...")
    code = await _poll_for_reply(offset, pattern=r"^\d{5,6}$", timeout=120)
    logger.info(f"✅ Otrzymano kod SMS")
    return code


async def _ask_for_2fa_password() -> str:
    """Wysyła prośbę o hasło 2FA na kanał i czeka na odpowiedź."""
    offset = await _get_current_offset()
    await _bot_send(
        "🔐 Konto ma włączone 2FA.\n\n"
        "Wpisz tutaj *hasło 2FA* do konta Telegram:"
    )
    logger.info("⏳ Czekam na hasło 2FA na kanale recive-bot-investor...")
    password = await _poll_for_reply(offset, pattern=r".{3,}", timeout=120)
    logger.info("✅ Otrzymano hasło 2FA")
    return password


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_topic_id(msg: Message) -> int | None:
    """Zwraca ID tematu forum (top_id) z wiadomości."""
    if not msg.reply_to:
        return None
    top    = getattr(msg.reply_to, "reply_to_top_id", None)
    msg_id = getattr(msg.reply_to, "reply_to_msg_id", None)
    return top or msg_id


def is_watched_topic(msg: Message) -> bool:
    """True jeśli wiadomość pochodzi z tematu IKE lub IKZE."""
    return get_topic_id(msg) in (IKE_TOPIC_ID, IKZE_TOPIC_ID)


def parse_fetch_command(text: str) -> tuple[int | None, int]:
    """
    Parsuje komendę fetch z tekstu.
    Zwraca (topic_id, count) lub (None, 0).

    Obsługiwane formaty:
        /fetch IKE 22  |  /fetch IKZE 15  |  weź 10 ostatnich z IKE  |  ike 22
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
    """Forwarduje nowe wiadomości z IKE/IKZE do test-bot-inwestor."""
    msg: Message = event.message

    if not is_watched_topic(msg):
        return

    topic_id   = get_topic_id(msg)
    topic_name = TOPIC_NAMES.get(topic_id, str(topic_id))

    logger.info(
        f"📩 [{topic_name}] id={msg.id} | "
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


async def fetch_and_forward(client: TelegramClient, topic_id: int, count: int) -> int:
    """Pobiera ostatnie `count` wiadomości z tematu → forward do test-bot-inwestor."""
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

    message_ids.reverse()  # chronologicznie

    forwarded = 0
    for msg_id in message_ids:
        try:
            await client.forward_messages(
                entity=STAGING_CHANNEL,
                messages=msg_id,
                from_peer=DAMIAN_GROUP_ID,
            )
            forwarded += 1
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"❌ Błąd forwardu msg {msg_id}: {e}")

    logger.success(f"✅ Fetch [{topic_name}]: {forwarded}/{len(message_ids)} → test-bot-inwestor")
    return forwarded


async def handle_user_command(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """Obsługuje /fetch IKE N z kanału recive-bot-investor."""
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

    logger.add(
        str(LOGS_DIR / "damian_watcher_{time:YYYY-MM-DD}.log"),
        rotation="1 day",
        retention="7 days",
        level="DEBUG",
        encoding="utf-8",
    )

    if not DAMIAN_GROUP_ID:
        logger.error("DAMIAN_GROUP_ID nie ustawiony w .env — kończę")
        return

    client = build_client()

    # Rejestruj handlery przed startem
    @client.on(events.NewMessage(chats=DAMIAN_GROUP_ID))
    async def _on_damian(event):
        await handle_new_message(event, client)

    if OUTPUT_CHANNEL:
        @client.on(events.NewMessage(chats=OUTPUT_CHANNEL))
        async def _on_command(event):
            await handle_user_command(event, client)

    # Logowanie — kod SMS przychodzi przez kanał Telegram
    await client.start(
        phone=settings.userbot_phone,
        code_callback=_ask_for_sms_code,
        password=_ask_for_2fa_password,
    )

    me = await client.get_me()
    logger.info(f"🚀 Damian Watcher uruchomiony jako: {me.first_name} (@{me.username})")
    logger.info(f"   Grupa Damiana: {DAMIAN_GROUP_ID}")
    logger.info(f"   IKE: {IKE_TOPIC_ID} | IKZE: {IKZE_TOPIC_ID}")
    logger.info(f"   Staging: {STAGING_CHANNEL} | Output: {OUTPUT_CHANNEL}")
    logger.info("👂 Nasłuchuję...")

    await _bot_send("✅ *damian\\_watcher* uruchomiony i nasłuchuje IKE/IKZE.\nKomenda: `/fetch IKE 5`")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
