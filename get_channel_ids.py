"""
Helper — znajdź ID kanałów z linków invite.
Uruchom: python get_channel_ids.py

Przy pierwszym uruchomieniu zapyta o numer telefonu + kod SMS.
Plik sesji zostanie zapisany jako signal_copier.session
"""

import asyncio
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

API_ID   = 36661880
API_HASH = "f849584c847a5a892abd2f683838c76a"

# ← Wpisz tutaj swoje linki invite
INVITE_LINKS = [
    "https://t.me/+3Tn1wpYFUlAwOTE0",   # Test-bot-inwestor (źródło)
    "https://t.me/+q0z9RRgeEnMyNzk0",   # recive bot investor (cel)
]


async def resolve_link(client: TelegramClient, link: str) -> None:
    """Wypisuje ID kanału z linku invite lub @username."""
    print(f"\n🔍 Sprawdzam: {link}")
    try:
        if "t.me/+" in link or "t.me/joinchat/" in link:
            # Prywatny kanał — hash z linku invite
            hash_part = link.split("/+")[-1] if "/+" in link else link.split("/joinchat/")[-1]
            try:
                result = await client(ImportChatInviteRequest(hash_part))
                chat = result.chats[0]
            except UserAlreadyParticipantError:
                # Już jesteś w kanale — pobierz info inaczej
                entity = await client.get_entity(link)
                chat = entity
        else:
            # Publiczny kanał (@username)
            entity = await client.get_entity(link)
            chat = entity

        full = await client(GetFullChannelRequest(channel=chat))
        cid = chat.id
        title = getattr(chat, "title", "?")
        # Kanały mają ID bez prefiksu -100 w Telethon — dodaj go ręcznie
        real_id = int(f"-100{cid}")
        print(f"  ✅ Nazwa:   {title}")
        print(f"  📌 ID (do .env): {real_id}")

    except Exception as e:
        print(f"  ❌ Błąd: {e}")


async def main():
    print("=" * 50)
    print("  Telegram Channel ID Finder")
    print("=" * 50)
    async with TelegramClient("signal_copier", API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"\n✅ Zalogowano jako: {me.first_name} (@{me.username})")
        for link in INVITE_LINKS:
            await resolve_link(client, link)
    print("\n📋 Skopiuj powyższe ID do pliku .env")


if __name__ == "__main__":
    asyncio.run(main())
