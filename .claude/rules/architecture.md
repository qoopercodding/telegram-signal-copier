# Architektura systemu

## Pipeline (aktualny, po refaktorze 2026-04-26)

```
DamianInwestorx (prywatna supergroup)
  topics: IKE (8951), IKZE (8953)
    ↓ events.NewMessage + is_watched_topic()
    ↓ _damian_handler → _forward_to_staging()
    ↓ client.forward_messages()
    
test-bot-inwestor (broadcast channel, SOURCE_GROUP_ID)
    ↓ _staging_poll_loop() co 15s  ← GŁÓWNY mechanizm
    ↓ events.NewMessage  ← backup (niezbyt niezawodny dla broadcast)
    ↓ _process_message()
    ↓ analyze_message() → Gemini 2.5-flash
    ↓ send_signal_notification()
    
recive-bot-investor (broadcast channel, RAW_CHANNEL_ID)
  ← AI wyniki z pipeline
  ← odpowiedzi na pytania Marcina
  
  Marcin pisze pytanie/zdjęcie/PLN kwotę
    ↓ _output_poll_loop() co 15s  ← GŁÓWNY mechanizm
    ↓ events.NewMessage  ← backup
    ↓ handle_channel_message()
    ↓ /fetch | PLN advisor | AI Q&A | zdjęcie analiza
```

## Dwa procesy

### signal-copier (listener.py) — Telethon userbot @QooperBoy
- Jedno konto, jedna sesja: `signal_copier.session`
- Handler 1: `events.NewMessage(chats=SOURCE_GROUP_ID)` → AI pipeline
- Handler 2: `events.NewMessage(chats=DAMIAN_GROUP_ID)` → forward do staging
- Handler 3: `events.NewMessage(chats=RAW_CHANNEL_ID)` → odpowiedzi Marcina
- Poll staging: `_staging_poll_loop()` co 15s — fallback dla broadcast
- Poll output: `_output_poll_loop()` co 15s — fallback dla broadcast
- Poll fetch IPC: `_fetch_loop()` co 5s — czyta /tmp/.fetch_request.json

### signal-monitor (monitor_bot.py) — @signal_copier_monitor_bot (Bot API)
- TYLKO komendy DM od admina: /start /status /logs /disk /health /cleanup
- heartbeat_checker() — alert gdy signal-copier martwy >30 min
- NIE nasłuchuje żadnych kanałów

## Kluczowe decyzje techniczne

### Broadcast channels → polling zamiast events
**Problem:** Telethon's `events.NewMessage` dla broadcast channels wymaga zainicjowanego PTS tracking.
Po restarcie serwisu bot gubił eventy dopóki nie wywołano `get_entity()`.
**Rozwiązanie:** `get_entity()` przy starcie (ciepły cache) + poll loop co 15s jako niezawodny fallback.
Deduplication: `save_raw_message()` zwraca False dla duplikatów — obsługa w `_process_message()`.

### Bot API nie może odbierać updateów z broadcast channels
**Problem:** Telegram Bot API wymaga uprawnień admina na kanale broadcast żeby bot odbierał wiadomości.
Jeśli bot jest zwykłym memberem → nie dostaje nic.
**Rozwiązanie:** Cała logika odbioru przeniesiona do userbota (Telethon). Monitor_bot tylko DM.

### msg.out = True dla wiadomości Marcina
**Problem:** Userbot chodzi jako konto QooperBoy. Marcin też pisze jako QooperBoy.
`msg.out = True` dla OBU: własnych odpowiedzi AI i pytań Marcina.
**Rozwiązanie:** `_bot_sent_ids: set[int]` — śledzi ID wiadomości wysłanych przez bota.
Handler sprawdza `if msg.id in _bot_sent_ids: return` zamiast `if msg.out: return`.

### IPC dla /fetch
Monitor_bot (Bot API, Telegram DM) → pisze `/tmp/.fetch_request.json` → signal-copier czyta co 5s.
Użyte bo: bot może przyjąć komendę DM, ale nie może sam pobierać z prywatnej grupy.

## AI pipeline

```python
analyze_message(text, media_paths) → ai_result dict
  ↓ Gemini 2.5-flash (primary)
  ↓ fallback: Gemini 2.0-flash → Claude Haiku → GPT-4o-mini
  
ai_result = {
  "message_type": "TRADE_ACTION" | "PORTFOLIO_UPDATE" | "INFORMATIONAL" | "UNKNOWN",
  "confidence": 0.0–1.0,
  "summary": str,
  "trade_signal": {"action": "KUPNO"|"SPRZEDAŻ", "ticker": str, ...},
  "portfolio_positions": [{"ticker": str, "percentage": float, ...}],
  "ai_model": str,
}
```

Powiadomienie wysyłane gdy: `confidence >= 0.6` AND `type in (TRADE_ACTION, PORTFOLIO_UPDATE, INFORMATIONAL)`.

## Advisor (kalkulator pozycji)

Marcin pisze "50000 PLN" na recive-bot-investor:
→ `parse_cash_amount()` wykrywa kwotę
→ `build_advisor_message()` pobiera ostatnie pozycje z DB (`get_latest_trader_positions()`)
→ `get_share_price()` (stooq → yfinance) dla każdego tickera
→ zwraca ile sztuk kupić za daną kwotę
