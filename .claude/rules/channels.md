# Kanały Telegram, sesje, .env

## Kanały (zweryfikowane 2026-04-26)

| Kanał | ID | Link | .env key | Typ |
|---|---|---|---|---|
| test-bot-inwestor (staging) | -1003728819658 | https://t.me/+3Tn1wpYFUlAwOTE0 | SOURCE_GROUP_ID | broadcast |
| recive-bot-investor (output AI) | -1003925454327 | https://t.me/+q0z9RRgeEnMyNzk0 | RAW_CHANNEL_ID | broadcast |
| DamianInwestorx | -1001548727545 | prywatna | DAMIAN_GROUP_ID | supergroup |
| DamianInwestorx — temat IKE | topic 8951 | https://t.me/c/1548727545/8951 | DAMIAN_IKE_TOPIC_ID | — |
| DamianInwestorx — temat IKZE | topic 8953 | https://t.me/c/1548727545/8953 | DAMIAN_IKZE_TOPIC_ID | — |


## Sesje Telethon

- `signal_copier.session` → używana przez `listener.py` (serwis signal-copier)
- `damian_watcher.session` → używana przez `damian_watcher.py` (helper)
- MUSZĄ być osobne — SQLite lock

**Ważne przy testach:** nie używaj signal_copier.session z testu gdy serwis działa.
Zamiast tego: `shutil.copy2(session, "/tmp/test_pipeline_session.session")`.

## .env — klucze (NIE wpisuj tu wartości — secrets tylko w pliku .env na VM)

```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
BOT_TOKEN=...          # @signal_copier_monitor_bot
GEMINI_API_KEY=...     # Google AI Studio → wymień jeśli skompromitowany
DECISION_CHAT_ID=1463931213
DAMIAN_GROUP_ID=-1001548727545
SOURCE_GROUP_ID=-1003728819658
RAW_CHANNEL_ID=-1003925454327
MY_PORTFOLIO_SIZE=100000
SIGNAL_TTL_MINUTES=15
```

**Brakujące klucze (AI fallbacki):** ANTHROPIC_API_KEY, OPENAI_API_KEY — nie ustawione.
Używany jest tylko Gemini.

## VM

- Ścieżka projektu: `/home/marcin/telegram-signal-copier`
- Venv: `venv/bin/activate`
- Serwisy systemd: `signal-copier`, `signal-monitor`
- GitHub push: `git push origin main` (token w URL remote)

## Boty Telegram

- `@signal_copier_monitor_bot` — monitor bot (Bot API), token w BOT_TOKEN
- `@QooperBoy` — userbot (Telethon MTProto), konto Marcina
