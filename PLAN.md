# Plan rozwoju — telegram-signal-copier

> Zaktualizowany: 2026-04-25. Każdy task = jeden commit. Status: ✅ zrobione | 🔄 w toku | ⬜ todo

---

## FAZA 1 — Ceny i rozpoznawanie tickerów GPW

### T01 ✅ Rozszerzyć _GPW_MAP o brakujące spółki
**Cel:** Damian pisze "Polsat", "Orlen", "Dino" bez tickerów — bot musi to rozpoznać.
**Plik:** `src/prices.py`
**Kroki:**
- Pobrać pełną listę spółek GPW ze stooq.com (csv ~500 spółek)
- Wygenerować mapę {nazwa_uproszczona: ticker} dla 200+ najpopularniejszych
- Dodać skróty: "Polsat"→CPS, "Orlen"→PKN, "Miedź"→KGH, "Pekao"→PEO, "Dino"→DNP

### T02 ✅ Fuzzy matching nazw spółek gdy nie ma w mapie
**Cel:** "Cyfrowy Polsat" vs "CyfrowyPolsat" vs "polsat" → wszystko trafia do CPS.
**Plik:** `src/prices.py` — nowa funkcja `fuzzy_resolve_ticker(name)`
**Kroki:**
- Użyć `difflib.get_close_matches()` na kluczach `_GPW_MAP`, próg 0.75
- Fallback: zwróć oryginalną nazwę

### T03 ✅ Przestawić główne źródło cen na stooq.pl (zamiast yfinance)
**Cel:** yfinance często nie ma małych spółek GPW; stooq.pl ma prawie wszystkie.
**Plik:** `src/prices.py`
**Kroki:**
- Zmienić kolejność: stooq PIERWSZA, yfinance jako fallback (zamiana miejscami)
- Przetestować: XTB, CPS, DNP, ALE, CDR — wszystkie powinny mieć kurs

### T04 ⬜ Dodać bankier.pl jako trzecie źródło cen
**Plik:** `src/prices.py` — nowa funkcja `_try_bankier(symbol)`
**API:** `https://www.bankier.pl/new-charts/last-ratio?symbol={SYM}&intraday=true`
**Kroki:**
- Parsować JSON `{"p": kurs}` z odpowiedzi
- Dodać do łańcucha fallback jako trzeci

### T05 ⬜ AI-assisted ticker resolution gdy nazwa całkowicie nieznana
**Cel:** Gdy fuzzy_resolve_ticker nie znajdzie → Gemini powie co to za spółka.
**Plik:** `src/parser.py` + `src/prices.py`
**Kroki:**
- Krótki prompt: "Podaj ticker GPW dla: '{name}'. Odpowiedz TYLKO tickerem lub NULL."
- Cache wyników w słowniku sesji (nie pytać 2× o to samo)
- Wywołać gdy ticker z AI nie przechodzi walidacji yfinance

---

## FAZA 2 — Lepsza filtracja wiadomości

### T06 ✅ Ulepszyć prompt AI — dodać typ INFORMATIONAL
**Cel:** "PKN dalej spada" to nie jest sygnał — nie wysyłaj powiadomienia.
**Plik:** `src/parser.py` — `CLASSIFY_PROMPT`
**Kroki:**
- Dodać `INFORMATIONAL` do listy typów w prompcie z przykładami
- Przykłady INFORMATIONAL: "Rynek nerwowy", "Obserwuję XTB", komentarze bez akcji
- Instrukcja: dla INFORMATIONAL zawsze `trade_signal = null`, `confidence > 0.8`

### T07 ✅ Dodać INFORMATIONAL i TRANSACTION_HISTORY do MessageType enum
**Plik:** `src/models.py`
**Kroki:**
- `INFORMATIONAL = "INFORMATIONAL"`
- `TRANSACTION_HISTORY = "TRANSACTION_HISTORY"` (tabela transakcji z brokera — nie portfel)

### T08 ⬜ Zapisywać screenshoty portfela do osobnej tabeli DB
**Cel:** Historia jak portfel tradera zmieniał się w czasie.
**Plik:** `src/storage.py` — nowa tabela `portfolio_snapshots`
**Kolumny:** `id, message_id, chat_id, source_topic, positions_json, media_path, created_at`
**Kroki:**
- Dodać `CREATE TABLE IF NOT EXISTS portfolio_snapshots ...`
- Wywołać `save_portfolio_snapshot()` gdy `PORTFOLIO_UPDATE` i jest media_path

---

## FAZA 3 — Interaktywność na recive-bot-investor

### T09 ✅ monitor_bot.py — obsługa zdjęć od Marcina na recive-bot-investor
**Cel:** Marcin wysyła screenshot portfela → bot analizuje i doradza.
**Plik:** `src/monitor_bot.py`
**Kroki:**
- W `cmd_advisor_channel` dodać obsługę `event.message.photo`
- Pobierz przez Telethon `client.download_media(msg, file=tmp_path)`
- Wywołaj `analyze_message(text=caption_or_none, media_paths=[tmp_path])`
- Odpowiedz z kalkulatorem jeśli AI = PORTFOLIO_UPDATE

### T10 ✅ Odpowiadać na pytania tekstowe od Marcina w wolnej formie
**Cel:** "Czy XTB wygląda teraz na kupno?" → bot odpowiada z kontekstem portfela.
**Plik:** `src/monitor_bot.py`
**Kroki:**
- Gdy tekst nie pasuje do `/advisor` ani do kwoty PLN → wyślij do Gemini
- Systemowy prompt: "Jesteś doradcą GPW. Portfel tradera (IKE): {positions_ike}. Portfel tradera (IKZE): {positions_ikze}."
- Limit: tylko gdy wiadomość > 10 znaków (żeby nie odpowiadać na emoji/ok/tak)

### T11 ✅ Analiza wiadomości wrzuconych bezpośrednio na recive-bot-investor
**Cel:** Forwarded/wklejony tekst od Marcina → AI analizuje jak z test-bot-inwestor.
**Plik:** `src/monitor_bot.py`
**Kroki:**
- Dodać flagę w .env: `ANALYZE_USER_MESSAGES=true`
- Gdy wiadomość od usera na recive-bot-investor i nie jest komendą → `analyze_message()`
- Wyniki wysyłać jako reply (nie jako nowe powiadomienie)

---

## FAZA 4 — Multi-provider AI fallback

### T12 ✅ Nowy plik src/ai_providers.py — abstrakcja dostawcy AI
**Cel:** Łatwa podmiana Gemini → Claude → OpenAI gdy jeden padnie.
**Plik:** `src/ai_providers.py` (nowy)
**Kroki:**
- Funkcja: `async def call_ai(prompt: str, images: list[bytes], model_hint: str) -> str`
- Każdy provider jako osobna async funkcja: `_call_gemini`, `_call_claude`, `_call_openai`
- Łańcuch: Gemini 2.5-flash → Gemini 2.0-flash → Claude Haiku → GPT-4o-mini

### T13 ✅ Dodać Anthropic Claude jako fallback
**Plik:** `src/ai_providers.py`
**Kroki:**
- `pip install anthropic` + dodać do requirements.txt
- `_call_claude(prompt, images)` przez `anthropic.AsyncAnthropic(api_key=...)`
- Model: `claude-haiku-4-5-20251001` (najszybszy, najtańszy)
- Dodać `ANTHROPIC_API_KEY=""` do `.env` i `config.py`

### T14 ✅ Dodać OpenAI GPT-4o-mini jako fallback
**Plik:** `src/ai_providers.py`
**Kroki:**
- `pip install openai` + dodać do requirements.txt
- `_call_openai(prompt, images)` przez `openai.AsyncOpenAI(api_key=...)`
- Model: `gpt-4o-mini`
- Dodać `OPENAI_API_KEY=""` do `.env` i `config.py`

### T15 ✅ Zaktualizować parser.py — używać ai_providers
**Plik:** `src/parser.py`
**Kroki:**
- Zastąpić pętlę modeli Gemini wywołaniem `call_ai(prompt, images)`
- Zachować retry logic i JSON parsing
- Zachować walidację tickera po wyniku AI

---

## FAZA 5 — IKE/IKZE niezawodność

### T16 ✅ Content-based wykrycie IKE vs IKZE ze screenshota
**Cel:** Damian używa różnych brokerów — layout inny, ale treść zawiera "IKE"/"IKZE".
**Plik:** `src/parser.py` — dodać do CLASSIFY_PROMPT
**Kroki:**
- Dodać pole `detected_account_type: "IKE" | "IKZE" | null` do JSON odpowiedzi AI
- Instrukcja: "Sprawdź czy na screenie lub w tekście widać słowo IKE lub IKZE"
- Jeśli wykryto → nadpisać `source_topic` w wyniku

### T17 ✅ Dodać kolumnę source_topic do ai_analyses w DB
**Plik:** `src/storage.py`
**Kroki:**
- Migracja: `ALTER TABLE ai_analyses ADD COLUMN source_topic TEXT`
- (SQLite nie ma IF NOT EXISTS dla ALTER → obsłużyć OperationalError)
- Przy `save_ai_analysis` zapisywać `ai_result.get("source_topic")`

### T18 ✅ Kontekst historyczny — ostatnie N wiadomości z tego samego konta
**Cel:** AI widzi poprzednie sygnały z IKE gdy analizuje nowy sygnał IKE.
**Plik:** `src/storage.py` + `src/parser.py`
**Kroki:**
- `get_recent_analyses(source_topic, limit=5)` → ostatnie 5 wpisów IKE lub IKZE
- Dodać do promptu: "Ostatnie akcje tradera z {source_topic}: {history_summary}"
- Limit: max 400 znaków kontekstu

---

## FAZA 6 — Jakość i monitoring

### T19 ⬜ Testy jednostkowe parser.py
**Plik:** `tests/test_parser.py`
**Kroki:**
- Mock `call_ai()` (nie wysyłaj prawdziwych zapytań)
- Test: poprawny JSON → poprawne pola, `TRADE_ACTION` z tickerem, `PORTFOLIO_UPDATE`
- Min. 5 przypadków testowych

### T20 ⬜ Testy jednostkowe prices.py
**Plik:** `tests/test_prices.py`
**Kroki:**
- Mock httpx calls
- Test: `resolve_ticker`, `fuzzy_resolve_ticker`, stooq CSV parsing

### T21 ⬜ Komenda /portfolio2 — historia snapshotów IKE i IKZE
**Plik:** `src/monitor_bot.py`
**Kroki:**
- Nowa komenda `/portfolio2` → ostatnie 3 snapshoty IKE i IKZE z datami
- Format: "IKE (2026-04-24): XTB 45%, CDR 30%..."

### T22 ⬜ Automatyczny cleanup mediów co 7 dni (nie 30)
**Plik:** `src/monitor_bot.py`
**Kroki:**
- `MEDIA_RETENTION_DAYS = 7`
- W heartbeat_loop: uruchom cleanup raz dziennie (sprawdź datę ostatniego)

---

## Kolejność (priorytety)

```
Sprint 1 — ceny + filtracja:   T01 T02 T03 T04 T05 T06 T07
Sprint 2 — interaktywność:      T08 T09 T10 T11
Sprint 3 — multi-AI:            T12 T13 T14 T15
Sprint 4 — IKE/IKZE:            T16 T17 T18
Sprint 5 — jakość:              T19 T20 T21 T22
```

---

## Zmienne środowiskowe do dodania (przy T13/T14)

```dotenv
ANTHROPIC_API_KEY=        # Claude Haiku fallback
OPENAI_API_KEY=           # GPT-4o-mini fallback
ANALYZE_USER_MESSAGES=false
```
