# 🧠 SKILLS.md — Best Practices dla Telegram Signal Copier

> Plik referencyjny dla tego projektu. Zanim napiszesz kod, przeczytaj sekcję której dotyczy.

---

## 📁 1. Struktura projektu

### Zasada: jeden plik = jedna odpowiedzialność

```
src/
├── listener.py      # TYLKO odbieranie wiadomości (Telethon)
├── storage.py       # TYLKO operacje na SQLite
├── parser.py        # TYLKO parsowanie sygnałów (LLM)
├── calculator.py    # TYLKO kalkulator pozycji
├── bot.py           # TYLKO Decision Bot (wysyłanie, przyciski)
├── watchdog.py      # TYLKO monitoring i alerty
└── models.py        # TYLKO Pydantic models (wspólne typy danych)
```

**❌ Nie rób tego:**
```python
# listener.py
async def handler(event):
    text = event.raw_text
    parsed = call_llm(text)       # NIE — to należy do parser.py
    save_to_db(parsed)            # NIE — to należy do storage.py
    send_to_bot(parsed)           # NIE — to należy do bot.py
```

**✅ Rób to:**
```python
# listener.py — tylko odbiera i przekazuje dalej
async def handler(event):
    await storage.save_raw(event)
    await parser.process(event.id)
```

---

## 🔐 2. Zarządzanie sekretami

### Zasada: żaden sekret nie trafia do kodu ani do gita

**Zawsze używaj `.env`:**
```python
# config.py — jeden plik do ładowania wszystkich zmiennych
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    telegram_api_id: int
    telegram_api_hash: str
    bot_token: str
    raw_channel_id: int
    decision_chat_id: int
    source_group_id: int
    mistral_api_key: str
    my_portfolio_size: float = 100000
    signal_ttl_minutes: int = 15

    class Config:
        env_file = ".env"

settings = Settings()
```

**❌ Nigdy tak:**
```python
api_id = 36661880           # hardcoded w pliku
api_hash = "abc123..."      # wycieknie do gita
```

**Pliki które MUSZĄ być w .gitignore:**
```
.env
*.session           # plik sesji Telethon
db/
media/
logs/
```

---

## ⚡ 3. Telethon — zasady użycia

### Zasada: zawsze async, zawsze context manager

**✅ Poprawny wzorzec:**
```python
# Jeden async def main(), jeden asyncio.run()
client = TelegramClient("session", api_id, api_hash)

async def main():
    async with client:
        await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
```

**❌ Nie rób tego (powoduje deadlock):**
```python
asyncio.run(setup())      # pierwsze run()
asyncio.run(main())       # drugie run() — błąd!
```

### Sesja (plik .session)

- Plik `session.session` = zapisane logowanie
- **Nigdy nie commituj** do gita
- Jeden klient = jeden plik sesji
- Dwa skrypty z tą samą sesją = błąd `sqlite locked`

### Pobieranie danych o chacie/senderze

Telethon nie zawsze dostarcza pełnych danych — trzeba je pobrać:
```python
async def handler(event):
    chat = await event.get_chat()      # zawsze await
    sender = await event.get_sender() # zawsze await
```

### Szybsze szyfrowanie (opcjonalne)

```bash
pip install cryptg   # przyspiesza Telethon ~3-5x przy mediach
```

### Obsługa media_group (kilka zdjęć naraz)

```python
from collections import defaultdict
import asyncio

_media_buffer = defaultdict(list)

async def handler(event):
    if event.grouped_id:
        _media_buffer[event.grouped_id].append(event)
        await asyncio.sleep(1.5)  # poczekaj na resztę zdjęć
        
        group = _media_buffer.pop(event.grouped_id, None)
        if group and group[0].id == event.id:
            await process_group(group)  # przetwarzaj całą grupę razem
    else:
        await process_group([event])
```

### Pobieranie mediów

```python
# Pobierz do konkretnego folderu
if event.message.media:
    path = await event.message.download_media(
        file=f"media/{event.message.id}"
    )
```

---

## 🗄️ 4. SQLite — zasady użycia

### Zasada: idempotencja i deduplication

```python
# message_id jako PRIMARY KEY — nie możesz zapisać tego samego dwa razy
CREATE TABLE signals (
    message_id  INTEGER PRIMARY KEY,   -- z Telegrama, nie UUID
    ...
)

# INSERT OR IGNORE zamiast INSERT — bezpieczne przy duplikatach
cursor.execute(
    "INSERT OR IGNORE INTO signals (message_id, ...) VALUES (?, ...)",
    (event.message.id, ...)
)
```

### Obsługa edycji wiadomości

```python
@client.on(events.MessageEdited(chats=SOURCE_GROUP_ID))
async def on_edit(event):
    # Zaktualizuj istniejący rekord zamiast tworzyć nowy
    storage.update_raw_text(event.message.id, event.raw_text)
```

### Zawsze używaj context manager dla połączeń

```python
# ✅ Dobrze — połączenie automatycznie zamknięte
with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    cursor.execute(...)
    conn.commit()

# ❌ Źle — możliwy wyciek połączenia
conn = sqlite3.connect(db_path)
cursor.execute(...)  # co jeśli tutaj wystąpi błąd?
```

---

## 🤖 5. LLM Parser — zasady użycia

### Zasada: zawsze walidacja przez Pydantic, zawsze obsługa null

**Model sygnału:**
```python
from pydantic import BaseModel
from typing import Optional, Literal

class TradeSignal(BaseModel):
    action: Literal["BUY", "SELL", "CLOSE", "REDUCE"] | None
    ticker: Optional[str]
    quantity: Optional[float]
    price: Optional[float]
    confidence: float        # 0.0 - 1.0
    reason: Optional[str]   # dlaczego LLM tak zinterpretował
```

**Prompt do LLM — kluczowe zasady:**
```
- Zwróć TYLKO JSON, bez żadnego tekstu przed ani po
- Jeśli nie jesteś pewien akcji — zwróć action: null
- Jeśli confidence < 0.8 — zwróć null dla niepewnych pól
- NIE zgaduj tickera jeśli nie jest jednoznaczny
- Wykryj negacje: "NOT BUY", "wouldn't buy", "avoid" = nie sygnał
```

**Walidacja odpowiedzi:**
```python
try:
    signal = TradeSignal.model_validate_json(llm_response)
    if signal.confidence < 0.8 or signal.action is None:
        signal.requires_review = True
except ValidationError:
    # LLM zwrócił niepoprawny JSON — oznacz jako błąd
    log_parse_error(raw_text, llm_response)
    return None
```

### Nigdy nie ufaj LLM ślepo

```python
# Zawsze sprawdzaj czy ticker istnieje (opcjonalnie)
KNOWN_TICKERS = {"AAPL", "TSLA", "GOOGL", ...}  # lub API giełdy
if signal.ticker not in KNOWN_TICKERS:
    signal.requires_review = True
```

---

## 📨 6. Telegram Bot — zasady użycia

### Zasada: zawsze pokazuj raw text użytkownikowi

```python
# Rekomendacja powinna zawierać:
text = (
    f"📊 *Nowy sygnał*\n\n"
    f"Akcja: `{signal.action}`\n"
    f"Ticker: `{signal.ticker}`\n"
    f"Pewność parsera: `{signal.confidence:.0%}`\n\n"
    f"📝 *Oryginalna wiadomość:*\n"
    f"`{signal.raw_text[:200]}`"   # zawsze pokazuj co parser dostał
)
```

### TTL na sygnały — zawsze ustawiaj

```python
# Sygnał starszy niż 15 minut = automatycznie EXPIRED
from datetime import datetime, timedelta

def is_expired(signal_timestamp: datetime, ttl_minutes: int = 15) -> bool:
    return datetime.utcnow() - signal_timestamp > timedelta(minutes=ttl_minutes)
```

### Rate limiting Telegram Bot API

```
Max 30 wiadomości/sekundę do różnych chatów
Max 1 wiadomość/sekundę do tego samego chatu
```

```python
import asyncio
await asyncio.sleep(1)  # między wiadomościami do tego samego chatu
```

### Forward vs. wyślij plik — obsługa błędów

```python
try:
    await bot.forward_message(
        chat_id=DECISION_CHAT_ID,
        from_chat_id=RAW_CHANNEL_ID,
        message_id=raw_msg_id
    )
except telegram.error.BadRequest:
    # Forward zablokowany — wyślij zapisane zdjęcie
    if media_path and Path(media_path).exists():
        await bot.send_photo(
            chat_id=DECISION_CHAT_ID,
            photo=open(media_path, "rb"),
            caption="📎 Forward zablokowany — zdjęcie lokalne"
        )
```

---

## 📊 7. Kalkulator pozycji — zasady

### Zasada: skaluj po %, nie po ilości

```python
# ❌ Złe — wymaga znajomości kapitału tradera
ratio = my_balance / assumed_trader_balance
my_qty = trader_qty * ratio

# ✅ Dobre — zakładasz że sygnał = X% jego portfela
ASSUMED_TRADE_PERCENT = 0.05  # zakładamy 5% portfela tradera

my_value = MY_PORTFOLIO_SIZE * ASSUMED_TRADE_PERCENT
my_qty = int(my_value / current_price)
```

### Zawsze stosuj limity

```python
MAX_POSITION_PERCENT = 0.10   # max 10% portfela na 1 pozycję
MIN_QUANTITY = 1               # min 1 sztuka

my_qty = min(my_qty, int(MY_PORTFOLIO_SIZE * MAX_POSITION_PERCENT / price))
my_qty = max(my_qty, MIN_QUANTITY)
```

---

## 🔍 8. Logowanie — zasady

### Używaj loguru zamiast print()

```python
from loguru import logger

# Konfiguracja raz w main.py
logger.add("logs/app.log", rotation="1 day", retention="7 days")
logger.add("logs/errors.log", level="ERROR", rotation="1 week")

# Użycie
logger.info(f"Odebrano sygnał: {signal.id}")
logger.warning(f"Niska pewność parsera: {signal.confidence}")
logger.error(f"Błąd parsowania: {e}")
```

### Zawsze loguj z kontekstem

```python
# ❌ Źle
logger.error("Błąd!")

# ✅ Dobrze
logger.error(f"Błąd parsowania message_id={msg_id}: {e}")
```

---

## 🚨 9. Obsługa błędów — zasady

### Zasada: nigdy nie pozwól żeby wyjątek zatrzymał listenera

```python
@client.on(events.NewMessage(chats=SOURCE_GROUP_ID))
async def handler(event):
    try:
        await process_message(event)
    except Exception as e:
        # Loguj błąd ale NIE reraisuj — listener musi działać dalej
        logger.error(f"Błąd przetwarzania {event.id}: {e}")
        await notify_developer(f"❌ Błąd: {e}\nMessage ID: {event.id}")
```

### Watchdog — zawsze implementuj

```python
# Sprawdzaj co 5 minut czy Telethon żyje
async def watchdog():
    while True:
        await asyncio.sleep(300)  # 5 minut
        if not client.is_connected():
            logger.critical("Telethon rozłączony!")
            await client.connect()
```

---

## 🔄 10. Git workflow — zasady

### Co commitować

```
✅ Kod źródłowy (src/)
✅ README.md, SKILLS.md, PLAN.md
✅ requirements.txt
✅ .env.example (NIGDY .env!)
✅ config.example.yaml

❌ .env
❌ *.session
❌ db/*.sqlite
❌ media/
❌ logs/
```

### Konwencja commitów

```
feat: dodano listener z obsługą media_group
fix: naprawiono duplikaty w SQLite
docs: zaktualizowano PLAN.md (etap 1 done)
refactor: wydzielono kalkulator do osobnego pliku
```

---

## ⚠️ 11. Pułapki specyficzne dla tego projektu

| Pułapka | Opis | Rozwiązanie |
|---|---|---|
| `session locked` | Dwa procesy używają tej samej sesji | Jeden proces = jedna sesja |
| `asyncio.run()` dwa razy | Deadlock w Telethon | Jeden `asyncio.run(main())` |
| Negacje w tekście | "NOT BUY TSLA" → regex matchuje "BUY TSLA" | LLM-first, prompt z negacjami |
| media_group split | 3 zdjęcia = 3 osobne eventy | Bufor 1.5s na grouped_id |
| Forward zablokowany | Prywatna grupa z restrict saving | Fallback do lokalnego pliku |
| Stary sygnał zatwierdzony | Cena zmieniła się o 5% | TTL 15 minut + auto-EXPIRED |
| api_hash w kodzie | Wyciek credentials | Zawsze .env, nigdy hardcode |
