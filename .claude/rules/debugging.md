# Znane pułapki i naprawione bugi

## Pułapki broadcast channels (Telethon)

### events.NewMessage nie strzela po restarcie
**Objaw:** Serwis startuje, logi pokazują "nasłuchuję", ale wiadomości na kanale nie są przetwarzane.
**Przyczyna:** Telethon potrzebuje zainicjowanego PTS tracking dla broadcast channels.
**Fix:** `await client.get_entity(channel_id)` dla każdego kanału zaraz po `async with client:`.
**Backup:** poll loop co 15s (`get_messages(min_id=last_id)`) — niezawodny fallback.

### msg.out = True dla wiadomości użytkownika
**Objaw:** Bot nie odpowiada na pytania Marcina na recive-bot-investor.
**Przyczyna:** Marcin i userbot to to samo konto (QooperBoy). Telegram oznacza wiadomości Marcina jako `out=True`.
**Fix:** Nie używaj `if msg.out: return`. Zamiast tego `_bot_sent_ids` — set ID wiadomości wysłanych przez kod bota.

### Pętla odpowiedzi
**Ryzyko:** Bot wysyła na recive-bot-investor → poll loop to widzi → wywołuje handle_channel_message → bot wysyła znowu...
**Fix:** `_send_to_raw()` helper — każda wysyłka bota rejestruje ID w `_bot_sent_ids`. Poll loop: `if msg.id in _bot_sent_ids: continue`.

## Sesja Telethon — konflikty

### SQLite lock przy testach
**Objaw:** `sqlite3.OperationalError: database is locked`
**Przyczyna:** `signal_copier.session` używana przez działający serwis.
**Fix:** W testach: `shutil.copy2("signal_copier.session", "/tmp/test_pipeline_session.session")`.

### Dwa klienty tego samego konta
**Objaw:** Dziwne zachowanie, zgubione wiadomości, "Another client is using this session"
**Fix:** Zawsze osobne pliki `.session`. Nigdy nie startuj dwóch procesów na tym samym pliku.

## Bot API na broadcast channels

**Objaw:** monitor_bot nie odbiera wiadomości z kanału.
**Przyczyna:** Bot API wymaga uprawnień admina na broadcast channel żeby odbierać updates.
**Fix:** Cała logika przeniesiona do userbota (Telethon). Bot API tylko DM.

## Gemini API

### Wycofany model
**Objaw:** `404 models/gemini-2.0-flash is not found`
**Fix:** Zmień na `gemini-2.5-flash` (aktualny stan w parser.py i listener.py).

### Timeout przy długich analizach
**Objaw:** `asyncio.TimeoutError` w logach, brak odpowiedzi na kanale.
**Fix:** `asyncio.wait_for(analyze_message(...), timeout=90)` w handle_channel_message.

### Frozen event loop
**Objaw:** Serwis żyje (process running), ale nic nie przetwarza, heartbeat przestaje się aktualizować.
**Przyczyna:** `event.reply()` lub inny await blokuje się indefinitely.
**Fix:** `asyncio.wait_for(..., timeout=120)` na każdym handlerze.

## Pricesy (stooq/yfinance)

### Brak ceny dla tickera GPW
**Objaw:** `get_share_price("PKO")` zwraca None.
**Przyczyna:** stooq wymaga sufiksu dla GPW: `pko.pl`. yfinance: `PKO.WA`.
**Fix:** `prices.py` próbuje oba formaty z fallbackiem.

## Uruchamianie testów

```bash
source venv/bin/activate
python test_pipeline.py
```

Testy wysyłają realne wiadomości na Telegram. TAG `[TEST HHMMSS]` identyfikuje każdy run.
Oczekiwany wynik: **5/5 OK** — T1 T2 T3 (staging) + T4/T5 (forward AI) + T6/T7 (AI Q&A).

Testy mogą trwać ~2-3 minuty (poll loops mają 15s interwał).

## Diagnoza problemów

```bash
# Logi na żywo
sudo journalctl -u signal-copier -f

# Ostatnie 50 linii
sudo journalctl -u signal-copier -n 50 --no-pager

# Czy serwis żyje?
sudo systemctl status signal-copier

# Heartbeat (co 5 min)
cat /home/marcin/telegram-signal-copier/.heartbeat | python3 -m json.tool
```

## Historia bugów (chronologicznie)

1. SOURCE_GROUP_ID wskazywał na DamianInwestorx zamiast test-bot-inwestor → fix: zmiana w .env
2. Bot API nie odbierał z broadcast channel → fix: pełen refaktor na userbot
3. `event.message.caption` AttributeError → fix: użyj `event.message.message` (Telethon ≠ Bot API)
4. Event loop freeze przez `event.reply()` bez timeout → fix: `asyncio.wait_for(timeout=90)` + watchdog
5. Sesja Telethon locked przez serwis przy testach → fix: `shutil.copy2` do /tmp
6. events.NewMessage nie strzela po restarcie (broadcast channel PTS) → fix: `get_entity()` + poll loop
7. `msg.out = True` blokował wiadomości Marcina → fix: `_bot_sent_ids` zamiast `msg.out`
