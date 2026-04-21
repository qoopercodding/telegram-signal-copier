# 📡 Telegram Signal Copier

System do kopiowania sygnałów tradingowych z kanału Telegram tradera.

## Co robi?

1. **Nasłuchuje** wiadomości z płatnej grupy Telegram (Telethon userbot)
2. **Klasyfikuje** typ wiadomości (aktualizacja portfela / akcja tradingowa / komentarz)
3. **Analizuje** screenshoty portfela i tabele transakcji (Mistral Pixtral Vision AI)
4. **Śledzi** stan portfela tradera — utrzymuje model z historią zmian
5. **Skaluje** propozycje do wielkości Twojego portfela
6. **Pyta Cię o zgodę** przez Telegram bota — ACCEPT / REJECT / SKIP
7. **Archiwizuje** wszystko w SQLite (pełna historia, debugowanie)

## Czego NIE robi

- ❌ Nie wykonuje transakcji automatycznie
- ❌ Nie łączy się z brokerem
- ❌ Nie obsługuje stop-lossów

## Architektura

```
[Kanał Telegram tradera — RESTRICTED]
         ↓
    Telethon Listener              ← userbot czyta wiadomości + media
         ↓
    Raw Storage (SQLite + media/)  ← archiwum WSZYSTKIEGO
         ↓
    Message Classifier (AI)        ← typ wiadomości
         ↓                    ↓
    Portfolio Tracker       Trade Signal Parser
    (trader_portfolio)      (ticker, qty, direction)
              ↓                    ↓
         Portfolio Differ       ← delta: co się zmieniło
              ↓
         Position Calculator    ← skalowanie do Twojego portfela
              ↓
         Decision Bot           ← propozycja + oryginał + [ACCEPT/REJECT]
              ↓
              Ty (human in the loop)
```

## Setup

### 1. Sklonuj repo
```bash
git clone https://github.com/qoopercodding/telegram-signal-copier.git
cd telegram-signal-copier
```

### 2. Stwórz virtualenv
```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac
```

### 3. Zainstaluj zależności
```bash
pip install -r requirements.txt
```

### 4. Skonfiguruj .env
```bash
copy .env.example .env
# Uzupełnij wartości w .env
```

### 5. Uruchom
```bash
python -m src.main
```

## Struktura projektu

```
├── src/
│   ├── __init__.py
│   ├── config.py          # Konfiguracja (Pydantic Settings + .env)
│   ├── models.py          # Modele danych (Pydantic)
│   ├── storage.py         # SQLite — zapis/odczyt
│   ├── listener.py        # Telethon — nasłuch wiadomości
│   ├── analyzer.py        # AI — klasyfikacja + parsowanie + vision
│   ├── differ.py          # Porównywanie stanów portfela
│   ├── calculator.py      # Skalowanie pozycji
│   ├── bot.py             # Decision Bot (Telegram)
│   ├── watchdog.py        # Monitoring i alerty
│   └── main.py            # Entry point
├── tests/
├── db/                    # SQLite (nie commitowane)
├── media/                 # Pobrane zdjęcia (nie commitowane)
├── logs/                  # Logi (nie commitowane)
├── .env.example           # Template zmiennych
├── .gitignore
├── requirements.txt
├── PLAN.md                # Szczegółowy plan projektu
├── SKILLS.md              # Best practices
└── README.md
```

## Technologie

- **Python 3.14+**
- **Telethon** — Telegram MTProto API (userbot)
- **Mistral AI (Pixtral)** — Vision AI do analizy screenshotów
- **Pydantic** — walidacja danych
- **SQLite** — lokalna baza danych
- **Loguru** — logowanie

## Rynek

Aktualnie obsługuje **GPW (Giełda Papierów Wartościowych)** — polskie akcje.
Docelowo skalowalne na inne rynki i kanały.