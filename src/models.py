"""
Modele danych projektu — Pydantic models.

Wspólne typy danych używane przez wszystkie moduły:
- RawMessage: surowa wiadomość z Telegrama
- TradeSignal: rozparsowany sygnał tradingowy
- TraderPosition: jedna pozycja w portfelu tradera
- TraderPortfolioSnapshot: stan portfela tradera w danym momencie
- PortfolioDelta: zmiana w portfelu (porównanie dwóch snapshotów)
- Recommendation: propozycja dla Ciebie (po skalowaniu)
- Decision: Twoja decyzja ACCEPT/REJECT
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# Enums
# ============================================================

class MessageType(str, Enum):
    """Typ wiadomości rozpoznany przez AI classifier."""
    PORTFOLIO_UPDATE = "PORTFOLIO_UPDATE"       # Screenshot/tekst z aktualnym portfelem
    TRADE_ACTION = "TRADE_ACTION"               # Kupno/sprzedaż/dobranie
    TRANSACTION_HISTORY = "TRANSACTION_HISTORY" # Tabela transakcji z brokera
    COMMENT = "COMMENT"                         # Komentarz bez sygnału
    UNKNOWN = "UNKNOWN"                         # Nie da się sklasyfikować


class TradeAction(str, Enum):
    """Akcja tradingowa."""
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"         # Zamknięcie całej pozycji
    REDUCE = "REDUCE"       # Zmniejszenie pozycji
    ADD = "ADD"             # Dobranie do pozycji


class DecisionStatus(str, Enum):
    """Status decyzji użytkownika."""
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SKIPPED = "SKIPPED"


# ============================================================
# Raw Message (z Telegrama)
# ============================================================

class RawMessage(BaseModel):
    """Surowa wiadomość odebrana z kanału tradera."""
    message_id: int                             # Telegram message ID
    chat_id: int                                # ID kanału/grupy źródłowej
    timestamp: datetime                         # Kiedy wysłana
    raw_text: Optional[str] = None              # Tekst wiadomości
    has_media: bool = False                     # Czy zawiera zdjęcie/dokument
    media_paths: list[str] = Field(default_factory=list)  # Ścieżki do pobranych mediów
    grouped_id: Optional[int] = None            # ID grupy (media_group)
    is_edit: bool = False                       # Czy to edycja istniejącej wiadomości


# ============================================================
# AI Classifier Result
# ============================================================

class ClassifiedMessage(BaseModel):
    """Wynik klasyfikacji wiadomości przez AI."""
    message_id: int
    message_type: MessageType
    confidence: float = Field(ge=0.0, le=1.0)   # Pewność klasyfikacji
    summary: Optional[str] = None                # Krótkie streszczenie co AI zrozumiał
    requires_review: bool = False                # Flaga: niska pewność → human review


# ============================================================
# Trade Signal (rozparsowany sygnał)
# ============================================================

class TradeSignal(BaseModel):
    """Rozparsowany sygnał tradingowy z wiadomości."""
    message_id: int
    action: Optional[TradeAction] = None
    ticker: Optional[str] = None                 # np. "XTB", "CRQUANTUM"
    quantity: Optional[float] = None             # Ilość sztuk (jeśli podana)
    price: Optional[float] = None                # Cena (jeśli podana)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: Optional[str] = None                 # Dlaczego AI tak zinterpretował
    raw_text_excerpt: Optional[str] = None       # Fragment oryginalnego tekstu
    requires_review: bool = False


# ============================================================
# Portfolio Tracking (stan portfela tradera)
# ============================================================

class TraderPosition(BaseModel):
    """Jedna pozycja w portfelu tradera."""
    ticker: str
    quantity: Optional[float] = None             # Ilość sztuk (jeśli znana)
    percentage: Optional[float] = None           # % portfela (0-100)
    value_pln: Optional[float] = None            # Wartość w PLN (jeśli znana)


class TraderPortfolioSnapshot(BaseModel):
    """Stan portfela tradera w danym momencie."""
    snapshot_id: Optional[int] = None
    timestamp: datetime
    source_message_id: int                       # Z której wiadomości pochodzi
    total_value_pln: Optional[float] = None      # Łączna wartość portfela
    positions: list[TraderPosition] = Field(default_factory=list)
    raw_source: Optional[str] = None             # Surowy tekst/opis źródła


# ============================================================
# Portfolio Delta (różnica między dwoma stanami)
# ============================================================

class PositionChange(BaseModel):
    """Zmiana jednej pozycji między dwoma snapshotami."""
    ticker: str
    old_percentage: Optional[float] = None
    new_percentage: Optional[float] = None
    change_percentage: Optional[float] = None    # delta (new - old)
    old_quantity: Optional[float] = None
    new_quantity: Optional[float] = None
    implied_action: Optional[TradeAction] = None # BUY/SELL/ADD/REDUCE/CLOSE


class PortfolioDelta(BaseModel):
    """Porównanie dwóch stanów portfela tradera."""
    old_snapshot_id: Optional[int] = None
    new_snapshot_id: Optional[int] = None
    timestamp: datetime
    changes: list[PositionChange] = Field(default_factory=list)
    new_positions: list[str] = Field(default_factory=list)       # Nowe tickery
    removed_positions: list[str] = Field(default_factory=list)   # Usunięte tickery


# ============================================================
# Recommendation (propozycja dla Ciebie)
# ============================================================

class Recommendation(BaseModel):
    """Propozycja zmiany w Twoim portfelu (po skalowaniu)."""
    signal_id: Optional[int] = None
    source_message_id: int
    ticker: str
    action: TradeAction
    suggested_quantity: int                       # Ilość do kupna/sprzedaży
    estimated_price: Optional[float] = None      # Szacunkowa cena
    estimated_value_pln: Optional[float] = None  # Szacunkowa wartość transakcji
    portfolio_percent: Optional[float] = None    # % Twojego portfela
    trader_context: Optional[str] = None         # Co trader zrobił (kontekst)
    original_text: Optional[str] = None          # Oryginalna wiadomość


# ============================================================
# Decision (Twoja decyzja)
# ============================================================

class Decision(BaseModel):
    """Decyzja użytkownika na propozycję bota."""
    decision_id: Optional[int] = None
    recommendation_id: Optional[int] = None
    source_message_id: int
    status: DecisionStatus = DecisionStatus.PENDING
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    bot_message_id: Optional[int] = None         # ID wiadomości bota (do edycji)
