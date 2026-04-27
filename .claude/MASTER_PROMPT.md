# Master Prompt — start sesji debugowania

Wklej to na początku nowej sesji Claude Code (po /clear):

---

Projekt: telegram-signal-copier (Python, Telethon, Gemini AI, systemd VM)

System kopiuje sygnały inwestycyjne z prywatnej grupy Telegram tradera Damiana
i przetwarza je przez AI → wyniki na kanale recive-bot-investor.

**Architektura:**
- signal-copier (listener.py) — Telethon userbot @QooperBoy, 3 handlery + 2 poll loops co 15s
- signal-monitor (monitor_bot.py) — Bot API, tylko DM komendy admina
- Pipeline: DamianInwestorx IKE/IKZE → test-bot-inwestor (staging) → Gemini 2.5-flash → recive-bot-investor
- Broadcast channels używają poll loop (nie events.NewMessage — zawodne po restarcie)

**Ostatni stan:** 5/5 testów E2E zielone (python test_pipeline.py)

**Do debugowania:**
[OPISZ PROBLEM TUTAJ]

**Żeby zobaczyć pełny kontekst przeczytaj:**
- .claude/rules/architecture.md — pipeline i kluczowe decyzje
- .claude/rules/channels.md — ID kanałów, .env, sesje
- .claude/rules/debugging.md — znane pułapki i historia bugów
- .claude/rules/handoff.md — aktualny stan i TODO

**Uruchomienie diagnostyki:**
```bash
sudo journalctl -u signal-copier -n 50 --no-pager
sudo systemctl status signal-copier signal-monitor
source venv/bin/activate && python test_pipeline.py
```

---
