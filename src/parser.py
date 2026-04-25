"""
AI Parser — analiza wiadomości tradera przez Google Gemini.

Co robi:
  1. Klasyfikuje wiadomość (PORTFOLIO_UPDATE / TRADE_ACTION / COMMENT / UNKNOWN)
  2. Dla TRADE_ACTION: wyciąga action, ticker, quantity, price
  3. Dla PORTFOLIO_UPDATE: wyciąga listę pozycji z screenshota
  4. Obsługuje zarówno tekst jak i zdjęcia (vision)

Użycie:
    from src.parser import analyze_message
    result = await analyze_message(text="Kupiłem 100 XTB", media_paths=[])
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from src.config import settings
from src.models import (
    MessageType,
    TradeAction,
    ClassifiedMessage,
    TradeSignal,
)

# Znane tickery GPW — szybka biała lista (bez opóźnienia sieciowego)
_GPW_KNOWN = {
    "XTB", "PKN", "KGHM", "CDR", "PKO", "PZU", "ALE", "DNP", "CCC", "LPP",
    "JSW", "PGE", "PGN", "OPL", "MBK", "SPL", "KGH", "PEO", "TPE", "ING",
    "BHW", "EUR", "PCO", "TEN", "KER", "VRG", "WPL", "ATT", "GPW", "CPS",
}


def _check_ticker_exists(ticker: str) -> bool:
    """Sprawdza czy ticker istnieje na GPW (.WA) lub globalnie przez yfinance."""
    if ticker.upper() in _GPW_KNOWN:
        return True
    try:
        import yfinance as yf
        # Najpierw GPW
        hist = yf.Ticker(f"{ticker}.WA").history(period="5d")
        if not hist.empty:
            return True
        # Potem globalnie
        hist = yf.Ticker(ticker).history(period="5d")
        return not hist.empty
    except Exception:
        return True  # Przy błędzie nie karz


async def _validate_ticker(ticker: str) -> bool:
    """Async wrapper dla _check_ticker_exists z timeoutem 10s."""
    import asyncio
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_check_ticker_exists, ticker),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Timeout walidacji tickera {ticker} — pomijam")
        return True


def get_client():
    """Zwraca klienta Gemini (dla prostych jednorazowych wywołań w monitor_bot)."""
    from google import genai
    return genai.Client(api_key=settings.gemini_api_key)


# ============================================================
# Prompty
# ============================================================

def _build_classify_prompt(source_topic: str | None = None) -> str:
    """Buduje prompt z rozmiarem portfela i opcjonalnym kontekstem historycznym."""
    from src.storage import get_recent_analyses

    portfolio_pln = settings.my_portfolio_size
    portfolio_note = (
        f"Portfel użytkownika do skalowania: {portfolio_pln:,.0f} PLN. "
        f"Dla PORTFOLIO_UPDATE — w polu 'summary' wylicz proporcjonalne kwoty dla każdej pozycji "
        f"(format: 'TICKER X% → Y PLN z Twojego portfela').\n\n"
    )

    history_note = ""
    if source_topic in ("IKE", "IKZE"):
        recent = get_recent_analyses(source_topic, limit=4)
        if recent:
            lines = [f"Ostatnie sygnały z konta {source_topic} (najnowsze pierwsze):"]
            for r in recent:
                action = r.get("action") or r.get("message_type", "?")
                ticker = r.get("ticker") or ""
                summary = (r.get("summary") or "")[:60]
                lines.append(f"  • {action} {ticker} — {summary}")
            history_note = "\n".join(lines) + "\n\n"

    return portfolio_note + history_note + CLASSIFY_PROMPT


CLASSIFY_PROMPT = """Jesteś asystentem analizującym wiadomości z kanału tradera na polskiej giełdzie (GPW).

Twoim zadaniem jest:
1. Sklasyfikować wiadomość
2. Wyciągnąć sygnał tradingowy (jeśli jest)

TYPY WIADOMOŚCI:
- TRADE_ACTION — trader WŁAŚNIE kupuje/sprzedaje/dodaje/redukuje pozycję (akcja w czasie teraźniejszym lub przeszłym bieżąca)
- PORTFOLIO_UPDATE — screenshot lub tekst pokazujący AKTUALNY stan portfela (jakie spółki trader TERAZ trzyma i w jakiej proporcji)
- TRANSACTION_HISTORY — tabela/historia transakcji z brokera (przeszłe transakcje, nie aktualny stan)
- INFORMATIONAL — komentarz rynkowy, opinia, obserwacja BEZ konkretnej akcji tradingowej
- UNKNOWN — nie da się sklasyfikować

KLUCZOWE ROZRÓŻNIENIA (najczęstsze błędy):
- "Kupiłem 100 XTB" = TRADE_ACTION (konkretna akcja)
- "XTB wygląda ciekawie, obserwuję" = INFORMATIONAL (nie kupuje, tylko obserwuje)
- "PKN dalej spada" = INFORMATIONAL (komentarz rynkowy)
- "Rynek nerwowy dziś" = INFORMATIONAL (ogólny komentarz)
- Screenshot z listą pozycji i procentami = PORTFOLIO_UPDATE
- Screenshot z historią transakcji (data, cena, ilość) = TRANSACTION_HISTORY
- "Mam plan dokupić CDR" = INFORMATIONAL (plan, nie akcja)
- "Dokupiłem CDR" = TRADE_ACTION (dokonana akcja)

ODPOWIEDZ W FORMACIE JSON (TYLKO JSON, bez markdown):
{
    "message_type": "TRADE_ACTION | PORTFOLIO_UPDATE | TRANSACTION_HISTORY | INFORMATIONAL | UNKNOWN",
    "confidence": 0.0-1.0,
    "summary": "krótkie streszczenie co zrozumiałeś (max 120 znaków)",
    "detected_account_type": "IKE | IKZE | null",
    "trade_signal": {
        "action": "BUY | SELL | CLOSE | REDUCE | ADD | null",
        "ticker": "ticker GPW (np. XTB, CDR, PKN) lub null — TYLKO jeśli TRADE_ACTION",
        "quantity": liczba lub null,
        "price": liczba lub null,
        "reason": "krótkie uzasadnienie interpretacji"
    },
    "portfolio_positions": [
        {"ticker": "XTB", "percentage": 86.64, "value_pln": 1335500}
    ]
}

WAŻNE ZASADY:
- Dla INFORMATIONAL: zawsze trade_signal = null, portfolio_positions = []
- Dla PORTFOLIO_UPDATE: wypisz WSZYSTKIE pozycje z tickerem i % udziału (value_pln jeśli widoczna)
- Dla innych typów: portfolio_positions = []
- Ticker: użyj skrótu GPW (XTB, CDR, PKN, KGH), nie pełnej nazwy
- Nazwy spółek: "Polsat"→CPS, "Orlen"→PKN, "Miedź"→KGH, "Dino"→DNP, "Pekao"→PEO
- detected_account_type: jeśli na screenie lub w tekście widać słowo "IKE" lub "IKZE" → wpisz je; inaczej null
- Jeśli nie jesteś pewien → confidence < 0.5, NIE ZGADUJ akcji tradingowej

WIADOMOŚĆ DO ANALIZY:
"""


# ============================================================
# Główna funkcja parsera
# ============================================================

async def analyze_message(
    text: Optional[str] = None,
    media_paths: Optional[list[str]] = None,
    source_topic: Optional[str] = None,
) -> dict:
    """
    Analizuje wiadomość tradera — tekst i/lub zdjęcia.
    Próbuje kolejne modele jeśli rate limit.

    Returns:
        Dict z kluczami: message_type, confidence, summary, trade_signal
    """
    import asyncio

    from src.ai_providers import call_ai

    # Buduj prompt i zbierz obrazy
    prompt = _build_classify_prompt(source_topic=source_topic)
    prompt += f"\n\nTEKST: {text}" if text else "\n\nTEKST: (brak tekstu — tylko media)"

    images: list[bytes] = []
    mime_types: list[str] = []
    _mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    if media_paths:
        for path_str in media_paths:
            path = Path(path_str)
            if path.exists() and path.suffix.lower() in _mime_map:
                try:
                    images.append(path.read_bytes())
                    mime_types.append(_mime_map[path.suffix.lower()])
                    logger.debug(f"📷 Dodano obraz do analizy: {path.name}")
                except Exception as e:
                    logger.error(f"Błąd ładowania obrazu {path}: {e}")

    raw_response = ""
    try:
        raw_response = await call_ai(prompt=prompt, images=images, mime_types=mime_types)
    except Exception as e:
        logger.error(f"❌ Wszystkie AI providers niedostępne: {e}")
        return {"message_type": "UNKNOWN", "confidence": 0.0, "summary": f"AI niedostępne: {str(e)[:80]}", "trade_signal": None}

    # Wyczyść ```json ... ``` jeśli model zwrócił blok kodu
    if raw_response.startswith("```"):
        raw_response = raw_response.split("\n", 1)[1]
        raw_response = raw_response.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError as e:
        logger.error(f"❌ AI zwrócił niepoprawny JSON: {e}\nRaw: {raw_response[:200]}")
        return {"message_type": "UNKNOWN", "confidence": 0.0, "summary": "Błąd parsowania odpowiedzi AI", "trade_signal": None}

    # Normalizuj ticker przez _GPW_MAP + walidacja
    if result.get("message_type") == "TRADE_ACTION":
        ts = result.get("trade_signal") or {}
        raw_ticker = ts.get("ticker")
        if raw_ticker:
            from src.prices import resolve_ticker
            normalized = resolve_ticker(raw_ticker)
            if normalized != raw_ticker:
                logger.debug(f"🔁 Ticker normalizacja: {raw_ticker} → {normalized}")
                ts["ticker"] = normalized
            valid = await _validate_ticker(normalized)
            if not valid:
                logger.warning(f"⚠️ Ticker {normalized} nieznany — obniżam confidence do 0.1")
                result["confidence"] = 0.1
                result["summary"] = f"[NIEZNANY TICKER: {normalized}] " + result.get("summary", "")

    # Jeśli AI wykryło IKE/IKZE ze screenshota → propaguj jako source_topic
    detected_account = result.get("detected_account_type")
    if detected_account in ("IKE", "IKZE") and not result.get("source_topic"):
        result["source_topic"] = detected_account
        logger.debug(f"🏷  AI wykryło konto ze screenshota: {detected_account}")

    logger.info(
        f"🤖 AI: {result.get('message_type', '?')} "
        f"(confidence={result.get('confidence', 0):.2f}) "
        f"— {result.get('summary', '?')[:80]}"
    )
    return result


# ============================================================
# Helpery do tworzenia modeli Pydantic z wyniku AI
# ============================================================

def parse_to_classified(message_id: int, ai_result: dict) -> ClassifiedMessage:
    """Konwertuje wynik AI na ClassifiedMessage."""
    return ClassifiedMessage(
        message_id=message_id,
        message_type=MessageType(ai_result.get("message_type", "UNKNOWN")),
        confidence=ai_result.get("confidence", 0.0),
        summary=ai_result.get("summary"),
        requires_review=ai_result.get("confidence", 0.0) < 0.7,
    )


def parse_to_signal(message_id: int, ai_result: dict) -> Optional[TradeSignal]:
    """Konwertuje wynik AI na TradeSignal (jeśli jest sygnał)."""
    ts = ai_result.get("trade_signal")
    if not ts or not ts.get("action"):
        return None

    try:
        return TradeSignal(
            message_id=message_id,
            action=TradeAction(ts["action"]) if ts.get("action") else None,
            ticker=ts.get("ticker"),
            quantity=ts.get("quantity"),
            price=ts.get("price"),
            confidence=ai_result.get("confidence", 0.0),
            reason=ts.get("reason"),
            requires_review=ai_result.get("confidence", 0.0) < 0.7,
        )
    except (ValueError, KeyError) as e:
        logger.warning(f"Nie udało się sparsować sygnału: {e}")
        return None
