# 📡 Telegram Signal Copier — Plan Projektu

## 🎯 Co budujemy?

System, który:
1. **Czyta wiadomości** z płatnej grupy Telegram (trader publikuje sygnały)
2. **Parsuje sygnały** (BUY/SELL, ticker, ilość, cena) — nawet z nieustrukturyzowanego tekstu i screenshotów
3. **Skaluje pozycję** do wielkości Twojego portfela
4. **Pyta Cię o zgodę** przez Telegram bota (ACCEPT / REJECT)
5. **Archiwizuje wszystko** w SQLite (pełna historia, możliwość debugowania)

### Czego NIE robi (na razie)
- ❌ Nie wykonuje transakcji automatycznie
- ❌ Nie łączy się z brokerem
- ❌ Nie obsługuje stop-lossów

---

## 🏗️ Architektura

```
[Grupa Telegram tradera]
        ↓
   Telethon Listener         ← czyta wiadomości jako user
        ↓
   RAW Channel               ← archiwum wszystkich wiadomości
        ↓
   SQLite Logger              ← zapis do bazy (raw_text, media, timestamp)
        ↓
   Parser (LLM-first)        ← Mistral API → Pydantic validation
        ↓
   Kalkulator pozycji         ← skalowanie % portfela
        ↓
   Decision Bot               ← Telegram bot → [ACCEPT] [REJECT]
        ↓
        Ty
```

---

## ✅ Co mamy już zrobione

| Element | Status |
|---|---|
| Repo na GitHub | ✅ |
| Struktura folderów lokalnie | ✅ |
| Telethon zainstalowany | ✅ |
| api_id i api_hash (nowe) | ✅ |
| Google Antigravity jako IDE | ✅ |
| Virtualenv | ⏳ do zrobienia |
| .env z credentials | ⏳ do zrobienia |
| Bot Token (@BotFather) | ⏳ do zrobienia |
| Mistral API Key | ⏳ do zrobienia |

---

## 🗺️ Plan działania — krok po kroku

---

### ETAP 0 — Przygotowanie środowiska
> Cel: masz działające środowisko, możesz uruchamiać kod

- [ ] **0.1** Otworzyć folder projektu w Antigravity
  - `C:\Users\Qoope\Documents\Antigravity\telegram-signal-copier`
- [ ] **0.2** Stworzyć virtualenv i zainstalować zależności
  ```
  python -m venv venv
  venv\Scripts\activate
  pip install telethon python-dotenv
  ```
- [ ] **0.3** Stworzyć plik `.env` z credentials
  ```
  TELEGRAM_API_ID=36661880
  TELEGRAM_API_HASH=twoj_nowy_hash
  ```
- [ ] **0.4** Upewnić się że `.env` jest w `.gitignore` (jest ✅)

---

### ETAP 1 — Telethon Listener
> Cel: widzisz wiadomości z grupy tradera w terminalu

- [ ] **1.1** Stworzyć `src/listener.py` — minimalny nasłuch wszystkich wiadomości
- [ ] **1.2** Uruchomić i zalogować się (numer telefonu + kod SMS)
  - Powstanie plik `session.session` — nie commituj go do gita!
- [ ] **1.3** Potwierdzić że wiadomości pojawiają się w terminalu
- [ ] **1.4** Dodać filtr — słuchać tylko konkretnej grupy tradera
- [ ] **1.5** Obsłużyć media_group (kilka zdjęć wysłanych naraz = jeden sygnał)

**Checkpoint:** widzę nowe wiadomości tradera w terminalu w czasie rzeczywistym

---

### ETAP 2 — RAW Channel + SQLite
> Cel: każda wiadomość jest bezpiecznie zapisana i możliwa do odtworzenia

- [ ] **2.1** Stworzyć prywatny kanał Telegram jako archiwum (RAW Channel)
- [ ] **2.2** Listener forwarduje każdą wiadomość do RAW Channel
- [ ] **2.3** Stworzyć `src/storage.py` — inicjalizacja SQLite, tabela `signals`
- [ ] **2.4** Każda wiadomość zapisywana do bazy:
  - `timestamp`, `raw_text`, `raw_channel_msg_id`, `media_paths`
- [ ] **2.5** Pobrać i zapisać lokalnie media (zdjęcia) z wiadomości
- [ ] **2.6** Deduplikacja po `message_id` (nie zapisuj dwa razy tego samego)

**Checkpoint:** wysyłam testową wiadomość → pojawia się w RAW Channel i w SQLite

---

### ETAP 3 — Parser sygnałów
> Cel: z tekstu wiadomości wyciągasz strukturę: action, ticker, qty, price

- [ ] **3.1** Zebrać ~20 przykładowych wiadomości od tradera (dane testowe)
- [ ] **3.2** Zdobyć Mistral API Key (console.mistral.ai)
- [ ] **3.3** Stworzyć `src/parser.py` — wywołanie LLM z Pydantic validation
- [ ] **3.4** Zdefiniować model `TradeSignal` (action, ticker, qty, price, confidence)
- [ ] **3.5** Odrzucać sygnały z `confidence < 0.8` → flaga `requires_review`
- [ ] **3.6** Obsłużyć `null` przy niepewności (LLM nie może zgadywać)
- [ ] **3.7** Przetestować na zebranych przykładach — mierzyć % poprawnych

**Checkpoint:** parser zwraca poprawny JSON dla 80%+ przykładowych wiadomości

---

### ETAP 4 — Kalkulator pozycji
> Cel: wiesz ile sztuk kupić proporcjonalnie do swojego portfela

- [ ] **4.1** Stworzyć `src/calculator.py`
- [ ] **4.2** Logika: zakładasz że sygnał tradera = X% jego portfela → kupujesz X% swojego
- [ ] **4.3** Ustawić `MAX_POSITION_PERCENT` (np. max 10% portfela na 1 pozycję)
- [ ] **4.4** Ustawić `MIN_QUANTITY` (nie kupuj mniej niż 1 szt.)
- [ ] **4.5** Przetestować na skrajnych wartościach (mały portfel, duży portfel)

**Checkpoint:** dla sygnału BUY AAPL 100 kalkulator zwraca sensowną ilość dla Twojego portfela

---

### ETAP 5 — Decision Bot
> Cel: dostajesz na Telegramie rekomendację i możesz ją zatwierdzić lub odrzucić

- [ ] **5.1** Stworzyć bota przez @BotFather → zapisać token do `.env`
- [ ] **5.2** Stworzyć `src/bot.py`
- [ ] **5.3** Bot wysyła wiadomość z rekomendacją:
  ```
  📊 Nowy sygnał
  Akcja: BUY
  Ticker: AAPL
  Trader: 100 szt.
  Ty: 20 szt.
  Cena: market
  ```
- [ ] **5.4** Dodać przyciski [✅ ACCEPT] [❌ REJECT]
- [ ] **5.5** Forward oryginalnej wiadomości tradera pod rekomendacją
- [ ] **5.6** Fallback: jeśli forward zablokowany → wyślij zapisane zdjęcie lokalnie
- [ ] **5.7** Zapisać decyzję do SQLite (`decision`, `decided_at`)
- [ ] **5.8** TTL na sygnały — jeśli nie odpiszesz w 15 min → auto EXPIRED

**Checkpoint:** dostaję wiadomość na Telegramie, klikam ACCEPT/REJECT, decyzja ląduje w bazie

---

### ETAP 6 — Watchdog i monitoring
> Cel: wiesz kiedy system przestał działać

- [ ] **6.1** Cron / pętla co 5 minut sprawdzająca czy Telethon żyje
- [ ] **6.2** Alert jeśli cisza z grupy tradera > 2 godziny (może coś padło)
- [ ] **6.3** Alert jeśli LLM API nie odpowiada
- [ ] **6.4** Restart automatyczny przy crashu (systemd na serwerze)

**Checkpoint:** wyłączam internet na 10 minut → dostaję alert na Telegramie

---

### ETAP 7 — Deployment na serwer
> Cel: bot działa 24/7 bez Twojego komputera

- [ ] **7.1** Skonfigurować SSH do własnego serwera VPS
- [ ] **7.2** `git push` z lokalnego → `git pull` na serwerze
- [ ] **7.3** Stworzyć `systemd` service — autostart po restarcie serwera
- [ ] **7.4** Przetestować pełny przepływ na produkcji

**Checkpoint:** wyłączam laptop → bot nadal działa i wysyła mi sygnały

---

## 🔐 Sekrety do zebrania

| Sekret | Skąd | Status |
|---|---|---|
| `TELEGRAM_API_ID` | my.telegram.org | ✅ masz |
| `TELEGRAM_API_HASH` | my.telegram.org | ✅ masz (nowy) |
| `BOT_TOKEN` | @BotFather na Telegramie | ⏳ |
| `RAW_CHANNEL_ID` | ID kanału archiwum | ⏳ |
| `DECISION_CHAT_ID` | Twoje chat ID | ⏳ |
| `SOURCE_GROUP_ID` | ID grupy tradera | ⏳ |
| `MISTRAL_API_KEY` | console.mistral.ai | ⏳ |

---

## ⚠️ Kluczowe ryzyka (pamiętaj o tym)

| Ryzyko | Jak mitygujemy |
|---|---|
| Regex matchuje negacje ("NOT BUY") | LLM-first, nie regex-first |
| Stary api_hash wyciekł publicznie | Wygenerowano nowy ✅ |
| Skalowanie po ilości bez znajomości kapitału | Skalujemy po % portfela |
| Forward zablokowany przez grupę | Fallback: zdjęcie lokalne |
| Bot przestaje działać bez alertu | Watchdog w Etapie 6 |
| TTL przekroczony, stary sygnał zatwierdzony | Auto-EXPIRED po 15 min |

---

## 🚀 Następny krok (teraz)

**Etap 0.2** — w terminalu Antigravity:

```bash
python -m venv venv
venv\Scripts\activate
pip install telethon python-dotenv
```
