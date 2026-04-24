# planTelethon.md — Watcher grup Damiana (IKE / IKZE)

## 1. Cel

Podpiąć Telethon userbot pod dwa tematy (topics) z prywatnej grupy Damiana:
- **IKE**: `https://t.me/c/1548727545/8951`
- **IKZE**: `https://t.me/c/1548727545/8953`

Każda nowa wiadomość (tekst, zdjęcie) z tych tematów ma automatycznie trafiać na kanał
**test-bot-inwestor** (`SOURCE_GROUP_ID`), skąd istniejący `listener.py` przejmuje
dalszy pipeline (AI → recive-bot-investor).

Dodatkowo: komenda z poziomu **recive-bot-investor** pobiera ostatnie N wiadomości
z wybranego tematu i forwarduje je do test-bot-inwestor w celu przetestowania AI.

---

## 2. Identyfikatory techniczne

| Zasób | URL | ID Telegram |
|---|---|---|
| Prywatna grupa Damiana | `t.me/c/1548727545/…` | `-1001548727545` |
| Temat IKE (portfel IKE) | `…/8951` | topic_id = `8951` |
| Temat IKZE (portfel IKZE) | `…/8953` | topic_id = `8953` |
| test-bot-inwestor (staging) | `t.me/+3Tn1wpYFUlAwOTE0` | `-1003728819658` |
| recive-bot-investor (output) | `t.me/+q0z9RRgeEnMyNzk0` | `-1003925454327` |

**Uwaga o topic_id w Telethon:**
Wiadomości w temacie forum mają:
```
msg.reply_to.reply_to_top_id  == 8951   # replies to replies
msg.reply_to.reply_to_msg_id  == 8951   # bezpośrednia odpowiedź na header tematu
```
Filtrowanie: `top_id in (8951, 8953)` gdzie `top_id = reply_to_top_id or reply_to_msg_id`.

---

## 3. Architektura — obecna vs. nowa

### Obecna
```
                                  listener.py (userbot)
[test-bot-inwestor] ──────────────►  AI (Gemini)
  (ręczny forward)                   SQLite
                                      ↓
                                 [recive-bot-investor]
                                   notifier.py (bot)
```

### Nowa
```
[Damian — prywatna grupa]
  │  topic IKE  (8951)      damian_watcher.py (userbot — ten sam .session)
  │  topic IKZE (8953)  ──►  nasłuch + forward
  │                               │
  │  nowe wiadomości live         │ forward_messages()
  │                               ▼
  │                      [test-bot-inwestor]
  │                               │
  │                               ▼
  │                        listener.py (bez zmian)
  │                          AI (Gemini) + SQLite
  │                               │
  │                               ▼
  │                      [recive-bot-investor]
  │                        notifier.py (bot)
  │
  └─ komenda użytkownika ─────────────────────────────────────────────────────►
     na recive-bot-investor:                                                    │
     "/fetch IKE 22"                                                            │
     "weź 15 ostatnich z IKZE"                                              damian_watcher
                                                                            pobiera historię
                                                                            → forward do test-bot-inwestor
```

---

## 4. Zmiany w .env

Dodać trzy nowe zmienne:

```dotenv
# Prywatna grupa Damiana + ID tematów
DAMIAN_GROUP_ID=-1001548727545
DAMIAN_IKE_TOPIC_ID=8951
DAMIAN_IKZE_TOPIC_ID=8953
```

---

## 5. Zmiany w src/config.py

Dodać trzy pola do klasy `Settings`:

```python
# --- Prywatna grupa Damiana (forum topics) ---
damian_group_id: int = 0
damian_ike_topic_id: int = 8951
damian_ikze_topic_id: int = 8953
```

---

## 6. Nowy plik: src/damian_watcher.py

### 6.1 Struktura modułu

```
src/damian_watcher.py
├── Stałe i config
├── build_client()           — ten sam Telethon session co listener
├── is_in_topic()            — filtr: czy msg należy do IKE/IKZE
├── handle_new_message()     — event handler nowych wiadomości z grupy Damiana
├── fetch_and_forward()      — pobiera ostatnie N wiadomości z tematu → forward
├── handle_user_command()    — parsuje komendy z recive-bot-investor
├── main()                   — uruchomienie + rejestracja handlerów
```

### 6.2 Pełny kod pliku

```python
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
"""

import asyncio
import re
from datetime import datetime

from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import Message

from src.config import settings, ensure_directories, LOGS_DIR


# ── Stałe ──────────────────────────────────────────────────────────────────

DAMIAN_GROUP_ID   = settings.damian_group_id        # -1001548727545
IKE_TOPIC_ID      = settings.damian_ike_topic_id    # 8951
IKZE_TOPIC_ID     = settings.damian_ikze_topic_id   # 8953
STAGING_CHANNEL   = settings.source_group_id        # test-bot-inwestor
OUTPUT_CHANNEL    = settings.raw_channel_id         # recive-bot-investor (komendy)

TOPIC_NAMES = {
    IKE_TOPIC_ID:  "IKE",
    IKZE_TOPIC_ID: "IKZE",
}

# Regex do parsowania komend fetch z wolnego tekstu
_FETCH_RE = re.compile(
    r'(?:/fetch\s+)?'                         # opcjonalne /fetch
    r'(ike|ikze)'                             # nazwa tematu
    r'[\s,]+(\d+)',                           # liczba wiadomości
    re.IGNORECASE,
)
# Alternatywny format: "weź 22 ostatnich z IKE"
_FETCH_RE2 = re.compile(
    r'we[zź]\s+(\d+)\s+\w+\s+z\s+(ike|ikze)',
    re.IGNORECASE,
)


# ── Klient Telethon ─────────────────────────────────────────────────────────

def build_client() -> TelegramClient:
    """Używa tej samej sesji co listener.py (signal_copier.session)."""
    session_path = str(LOGS_DIR.parent / settings.session_name)
    return TelegramClient(
        session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_topic_id(msg: Message) -> int | None:
    """
    Zwraca ID tematu (top_id) jeśli wiadomość należy do forum topic.
    Dla bezpośredniej odpowiedzi na header tematu: reply_to_msg_id == topic_id.
    Dla głębszych odpowiedzi:                      reply_to_top_id == topic_id.
    """
    if not msg.reply_to:
        return None
    top  = getattr(msg.reply_to, "reply_to_top_id", None)
    msg_id = getattr(msg.reply_to, "reply_to_msg_id", None)
    return top or msg_id


def is_watched_topic(msg: Message) -> bool:
    """True jeśli wiadomość pochodzi z tematu IKE lub IKZE."""
    tid = get_topic_id(msg)
    return tid in (IKE_TOPIC_ID, IKZE_TOPIC_ID)


def parse_fetch_command(text: str) -> tuple[int | None, int]:
    """
    Parsuje komendę fetch z tekstu.
    Zwraca (topic_id, count) lub (None, 0) jeśli nie znaleziono.

    Obsługiwane formaty:
        /fetch IKE 22
        /fetch IKZE 15
        weź 10 ostatnich z IKE
        ike 22
    """
    # Format 1: /fetch IKE 22 lub ike 22
    m = _FETCH_RE.search(text)
    if m:
        name  = m.group(1).upper()
        count = min(int(m.group(2)), 50)  # cap na 50
        topic_id = IKE_TOPIC_ID if name == "IKE" else IKZE_TOPIC_ID
        return topic_id, count

    # Format 2: weź 22 ostatnich z IKE
    m = _FETCH_RE2.search(text)
    if m:
        count = min(int(m.group(1)), 50)
        name  = m.group(2).upper()
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
        return  # Wiadomość z innego tematu — ignoruj

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

    forwarded = 0
    message_ids: list[int] = []

    # Zbierz ID wiadomości (iter_messages zwraca od najnowszych)
    async for msg in client.iter_messages(
        entity=DAMIAN_GROUP_ID,
        reply_to=topic_id,
        limit=count,
    ):
        message_ids.append(msg.id)

    if not message_ids:
        logger.warning(f"Brak wiadomości w [{topic_name}]")
        return 0

    # Forward w kolejności chronologicznej (odwróć listę)
    message_ids.reverse()

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
    Odpowiada na kanale potwierdzeniem i postępem.
    """
    text = (event.message.text or "").strip()
    if not text:
        return

    topic_id, count = parse_fetch_command(text)
    if not topic_id or count <= 0:
        return  # Nie jest komendą fetch — ignoruj

    topic_name = TOPIC_NAMES[topic_id]
    logger.info(f"📋 Komenda fetch: [{topic_name}] x{count} (od użytkownika na kanale)")

    # Potwierdź odbiór
    try:
        await client.send_message(
            OUTPUT_CHANNEL,
            f"⏳ Pobieram ostatnie **{count}** wiadomości z tematu **{topic_name}**...",
        )
    except Exception:
        pass  # Potwierdzenie opcjonalne

    forwarded = await fetch_and_forward(client, topic_id, count)

    # Raport końcowy
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

    # Handler 1 — nowe wiadomości z grupy Damiana
    @client.on(events.NewMessage(chats=DAMIAN_GROUP_ID))
    async def _on_damian(event):
        await handle_new_message(event, client)

    # Handler 2 — komendy użytkownika z recive-bot-investor
    if OUTPUT_CHANNEL:
        @client.on(events.NewMessage(chats=OUTPUT_CHANNEL))
        async def _on_command(event):
            await handle_user_command(event, client)

    async with client:
        me = await client.get_me()
        logger.info(f"🚀 Damian Watcher uruchomiony jako: {me.first_name} (@{me.username})")
        logger.info(f"   Grupa Damiana: {DAMIAN_GROUP_ID}")
        logger.info(f"   Tematyka IKE:  {IKE_TOPIC_ID} | IKZE: {IKZE_TOPIC_ID}")
        logger.info(f"   Staging:       {STAGING_CHANNEL} (test-bot-inwestor)")
        logger.info(f"   Komendy z:     {OUTPUT_CHANNEL} (recive-bot-investor)")
        logger.info("👂 Nasłuchuję... (Ctrl+C żeby zatrzymać)")
        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 7. Nowy plik systemd: /etc/systemd/system/damian-watcher.service

```ini
[Unit]
Description=Damian Watcher — Telethon IKE/IKZE forwarder
After=network.target signal-copier.service
Wants=signal-copier.service

[Service]
Type=simple
User=marcin
WorkingDirectory=/home/marcin/telegram-signal-copier
ExecStart=/home/marcin/telegram-signal-copier/venv/bin/python -m src.damian_watcher
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=damian-watcher

[Install]
WantedBy=multi-user.target
```

**Deploy:**
```bash
sudo cp damian-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable damian-watcher
sudo systemctl start damian-watcher
sudo systemctl status damian-watcher
```

---

## 8. Dodanie zmiennych do .env

```dotenv
# Prywatna grupa Damiana
DAMIAN_GROUP_ID=-1001548727545
DAMIAN_IKE_TOPIC_ID=8951
DAMIAN_IKZE_TOPIC_ID=8953
```

---

## 9. Dodanie pól do src/config.py (klasa Settings)

```python
# --- Prywatna grupa Damiana (forum topics) ---
damian_group_id: int = 0
damian_ike_topic_id: int = 8951
damian_ikze_topic_id: int = 8953
```

---

## 10. Komendy testowe (z kanału recive-bot-investor)

| Komenda | Efekt |
|---|---|
| `/fetch IKE 22` | Ostatnie 22 posty z IKE → test-bot-inwestor |
| `/fetch IKZE 10` | Ostatnie 10 postów z IKZE → test-bot-inwestor |
| `weź 5 ostatnich z IKE` | To samo — wolny tekst |
| `ike 15` | Skrócona forma |

Po każdej komendzie bot odpowiada na recive-bot-investor:
1. `⏳ Pobieram ostatnie 22 wiadomości z tematu IKE...`
2. `✅ Przesłano 22 wiadomości z [IKE] → test-bot-inwestor.`

Wiadomości trafiają do test-bot-inwestor → listener.py je przetwarza →
wyniki analizy AI pojawiają się na recive-bot-investor.

---

## 11. Pełny przepływ po zmianach

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DAMIAN — prywatna grupa                          │
│  [temat IKE 8951]          [temat IKZE 8953]                        │
│  Nowa wiadomość live        Nowa wiadomość live                      │
└────────────────┬────────────────────┬───────────────────────────────┘
                 │                    │
                 ▼                    ▼
        damian_watcher.py  (Telethon userbot — signal_copier.session)
        handle_new_message() → forward_messages()
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│              test-bot-inwestor  (staging)                          │
│  [forwarded message from IKE or IKZE]                              │
└────────────────┬───────────────────────────────────────────────────┘
                 │
                 ▼
        listener.py  (Telethon userbot — bez zmian)
        1. Zapisz do SQLite
        2. Pobierz media lokalnie
        3. Gemini AI analiza
        4. Zapisz portfolio_positions (jeśli PORTFOLIO_UPDATE)
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────┐
│              recive-bot-investor  (output)                         │
│  [screenshot + analiza AI]                                         │
│  [CO KUPIĆ: X szt. XTB, Y szt. CREOTECH, ...]                     │
└────────────────────────────────────────────────────────────────────┘

  ┌───────────────────────────────────────────────────────────────┐
  │  KOMENDA TESTOWA (recive-bot-investor → damian_watcher)       │
  │                                                               │
  │  Użytkownik pisze: "/fetch IKE 22"                           │
  │                         │                                     │
  │                         ▼                                     │
  │  damian_watcher.fetch_and_forward(IKE_TOPIC, 22)             │
  │    → iter_messages(damian_group, reply_to=8951, limit=22)    │
  │    → forward każdą wiadomość do test-bot-inwestor            │
  │    → listener.py przetwarza każdą przez AI                   │
  └───────────────────────────────────────────────────────────────┘
```

---

## 12. Uwagi implementacyjne

### Sesja Telethon — współdzielenie
`damian_watcher.py` używa **tej samej sesji** (`signal_copier.session`) co `listener.py`.
Telethon obsługuje wiele połączeń na tej samej sesji, ale **nie może być uruchomiony
dwukrotnie jednocześnie** z tym samym plikiem `.session`.

**Rozwiązanie:** `listener.py` i `damian_watcher.py` muszą używać **osobnych sesji**.
Dodaj do `.env`:
```dotenv
DAMIAN_SESSION_NAME=damian_watcher
```
I w `damian_watcher.py` użyj `settings.damian_session_name` zamiast `settings.session_name`.

Pierwsze uruchomienie `damian_watcher.py` poprosi o numer telefonu + kod SMS i
zapisze nową sesję do `damian_watcher.session`.

### Rate limiting przy fetch historii
`fetch_and_forward()` ma `await asyncio.sleep(0.3)` między forwardami (≈3 wiadomości/s).
Przy 50 wiadomościach = ~17 sekund. Jeśli Telegram zwróci FloodWait, należy
obsłużyć wyjątek `telethon.errors.FloodWaitError` i poczekać wymaganą liczbę sekund.

### Forum topics vs zwykłe grupy
URL `t.me/c/1548727545/8951`:
- `1548727545` = ID grupy (bez prefiksu `-100`)
- `8951` = ID pierwszej wiadomości tematu = `topic_id`

W Telethon pełne ID grupy = `-1001548727545`.

Filtr tematów:
```python
top_id = getattr(msg.reply_to, "reply_to_top_id", None)
         or getattr(msg.reply_to, "reply_to_msg_id", None)
if top_id in (8951, 8953):
    # to jest IKE lub IKZE
```

---

## 13. Pliki do stworzenia / zmodyfikowania

| Plik | Akcja |
|---|---|
| `src/damian_watcher.py` | **NOWY** — główny moduł |
| `src/config.py` | Dodać 3 pola do `Settings` |
| `.env` | Dodać 3 zmienne + `DAMIAN_SESSION_NAME` |
| `/etc/systemd/system/damian-watcher.service` | **NOWY** — systemd unit |

Istniejące pliki (`listener.py`, `notifier.py`, `monitor_bot.py`, `storage.py`, `prices.py`)
**pozostają bez zmian**.

---

## 14. Kolejność wdrożenia

1. Dodaj zmienne do `.env`
2. Dodaj pola do `src/config.py`
3. Stwórz `src/damian_watcher.py` (kod z sekcji 6.2)
4. Uruchom ręcznie: `venv/bin/python -m src.damian_watcher`
   → Zaloguje się (poprosi o kod SMS jeśli nowa sesja)
   → Potwierdź logowanie
5. Przetestuj komendę z recive-bot-investor: `/fetch IKE 5`
   → Sprawdź czy 5 wiadomości pojawiło się na test-bot-inwestor
   → Sprawdź czy AI przeanalizowało i wróciło na recive-bot-investor
6. Stwórz i włącz serwis systemd
7. Weryfikacja live: poczekaj na nową wiadomość Damiana lub wyślij testową
