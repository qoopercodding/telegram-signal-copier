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
import base64
from pathlib import Path
from typing import Optional

import google.generativeai as genai
from loguru import logger

from src.config import settings
from src.models import (
    MessageType,
    TradeAction,
    ClassifiedMessage,
    TradeSignal,
)


# ============================================================
# Konfiguracja Gemini
# ============================================================

def get_model() -> genai.GenerativeModel:
    """Zwraca skonfigurowany model Gemini."""
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel("gemini-2.0-flash")


# ============================================================
# Prompty
# ============================================================

CLASSIFY_PROMPT = """Jesteś asystentem analizującym wiadomości z kanału tradera na polskiej giełdzie (GPW).

Twoim zadaniem jest:
1. Sklasyfikować wiadomość
2. Wyciągnąć sygnał tradingowy (jeśli jest)

TYPY WIADOMOŚCI:
- TRADE_ACTION — trader kupuje/sprzedaje/dodaje/redukuje pozycję
- PORTFOLIO_UPDATE — screenshot lub tekst z aktualnym stanem portfela
- TRANSACTION_HISTORY — tabela transakcji z brokera
- COMMENT — komentarz, opinia, bez akcji
- UNKNOWN — nie da się sklasyfikować

ODPOWIEDZ W FORMACIE JSON (TYLKO JSON, bez markdown):
{
    "message_type": "TRADE_ACTION | PORTFOLIO_UPDATE | COMMENT | UNKNOWN",
    "confidence": 0.0-1.0,
    "summary": "krótkie streszczenie co zrozumiałeś",
    "trade_signal": {
        "action": "BUY | SELL | CLOSE | REDUCE | ADD | null",
        "ticker": "symbol akcji lub null",
        "quantity": liczba lub null,
        "price": liczba lub null,
        "reason": "dlaczego tak interpretujesz"
    }
}

WAŻNE ZASADY:
- Jeśli nie jesteś pewien, ustaw confidence < 0.5
- NIE ZGADUJ — jeśli nie ma jasnego sygnału, daj null
- Tickery na GPW: np. XTB, PKN, KGHM, CDR, PKO, PZU, ALE, DNP, CCC, LPP
- Trader może pisać po polsku: "kupiłem", "dokupiłem", "sprzedałem", "zamknąłem"
- "Dobieram" = ADD, "Redukcja" = REDUCE, "Zamykam pozycję" = CLOSE
- Jeśli jest screenshot — opisz co widzisz (portfel, transakcje, chart)

WIADOMOŚĆ DO ANALIZY:
"""


# ============================================================
# Główna funkcja parsera
# ============================================================

async def analyze_message(
    text: Optional[str] = None,
    media_paths: Optional[list[str]] = None,
) -> dict:
    """
    Analizuje wiadomość tradera — tekst i/lub zdjęcia.

    Returns:
        Dict z kluczami: message_type, confidence, summary, trade_signal
    """
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY nie ustawiony — pomijam analizę AI")
        return {
            "message_type": "UNKNOWN",
            "confidence": 0.0,
            "summary": "AI niedostępne — brak API key",
            "trade_signal": None,
        }

    model = get_model()
    content_parts = []

    # Dodaj tekst
    prompt = CLASSIFY_PROMPT
    if text:
        prompt += f"\n\nTEKST: {text}"
    else:
        prompt += "\n\nTEKST: (brak tekstu — tylko media)"

    content_parts.append(prompt)

    # Dodaj zdjęcia (vision)
    if media_paths:
        for path_str in media_paths:
            path = Path(path_str)
            if path.exists() and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                try:
                    image_data = path.read_bytes()
                    mime_type = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".webp": "image/webp",
                    }.get(path.suffix.lower(), "image/jpeg")

                    content_parts.append({
                        "mime_type": mime_type,
                        "data": image_data,
                    })
                    logger.debug(f"📷 Dodano obraz do analizy: {path.name}")
                except Exception as e:
                    logger.error(f"Błąd ładowania obrazu {path}: {e}")

    # Wywołaj Gemini
    try:
        response = await model.generate_content_async(content_parts)
        raw_response = response.text.strip()

        # Wyczyść response — Gemini czasem zwraca ```json ... ```
        if raw_response.startswith("```"):
            raw_response = raw_response.split("\n", 1)[1]  # Usuń ```json
            raw_response = raw_response.rsplit("```", 1)[0]  # Usuń końcowe ```
            raw_response = raw_response.strip()

        result = json.loads(raw_response)
        logger.info(
            f"🤖 AI: {result.get('message_type', '?')} "
            f"(confidence={result.get('confidence', 0):.2f}) "
            f"— {result.get('summary', '?')}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"❌ AI zwrócił niepoprawny JSON: {e}\nRaw: {raw_response[:200]}")
        return {
            "message_type": "UNKNOWN",
            "confidence": 0.0,
            "summary": f"Błąd parsowania odpowiedzi AI",
            "trade_signal": None,
        }
    except Exception as e:
        logger.error(f"❌ Błąd Gemini API: {e}")
        return {
            "message_type": "UNKNOWN",
            "confidence": 0.0,
            "summary": f"Błąd API: {str(e)[:100]}",
            "trade_signal": None,
        }


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
