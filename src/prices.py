"""
Pobieranie aktualnych kursów akcji — yfinance + stooq.pl fallback.

Kolejność dla każdego tickera:
  1. yfinance  → {symbol}.WA  (GPW przez Yahoo Finance)
  2. yfinance  → {symbol}     (globalnie, np. CDR)
  3. stooq.pl  → {symbol}.pl  (polski agregator GPW)
"""

import httpx
from loguru import logger


# ── Mapa: nazwa z AI → symbol GPW ────────────────────────────────────────────
# AI często zwraca pełne nazwy lub skróty inne niż ticker na GPW.
_GPW_MAP: dict[str, str] = {
    # CD Projekt
    "CDPROJEKT": "CDR", "CD PROJEKT": "CDR", "CDPROJEKT RED": "CDR", "CDPR": "CDR",
    # Cyfrowy Polsat
    "CYFRPLSAT": "CPS", "CYFROWY POLSAT": "CPS", "CYFROWYPOLSAT": "CPS",
    # Synektik
    "SYNEKTIK": "SNT",
    # Creotech
    "CREOTECH": "CRQ",
    # Vercom
    "VERCOM": "VRC",
    # Enter Air
    "ENTER": "ENA", "ENTER AIR": "ENA",
    # Best
    "BEST": "BST",
    # Orange Polska
    "ORANGE": "OPL", "ORANGE POLSKA": "OPL",
    # PKO BP
    "PKOBP": "PKO", "PKO BP": "PKO",
    # Pekao
    "PEKAO": "PEO", "BANK PEKAO": "PEO",
    # Tauron
    "TAURON": "TPE",
    # PGNiG
    "PGNIG": "PGN",
    # PGE
    "PGENERGIA": "PGE",
    # Dino Polska
    "DINO": "DNP", "DINO POLSKA": "DNP",
    # Allegro
    "ALLEGRO": "ALE",
    # Asseco Poland
    "ASSECO": "ACP", "ASSECO POLAND": "ACP",
    # KGHM
    "KGHM POLSKA MIEDZ": "KGH", "KGHM POLSKA": "KGH",
    # PKN Orlen
    "PKN ORLEN": "PKN", "PKNORLEN": "PKN", "ORLEN": "PKN",
    # Millennium
    "MILLENNIUM": "MIL", "MILLENIUM": "MIL", "BANK MILLENNIUM": "MIL",
    # Santander / BZ WBK
    "SANTANDER": "SPL",
    # mBank
    "MBANK": "MBK",
    # Benefit Systems
    "BENEFIT": "BFT", "BENEFIT SYSTEMS": "BFT",
    # LiveChat
    "LIVECHAT": "LVC",
    # Huuuge Games
    "HUUUGE": "HUG",
    # 11bit studios
    "11BIT": "11B", "11 BIT": "11B", "11 BIT STUDIOS": "11B",
    # Playway
    "PLAYWAY": "PLY",
    # Ten Square Games
    "TEN SQUARE": "TEN", "TENSQUARE": "TEN", "TEN SQUARE GAMES": "TEN",
    # Stalprodukt
    "STALPRODUKT": "STL",
    # Mercator Medical
    "MERCATOR": "MRC",
    # DataTalk
    "DATATALK": "DAT",
    # Amrest
    "AMREST": "EAT",
    # Budimex
    "BUDIMEX": "BDX",
    # Unimot
    "UNIMOT": "UNT",
    # Wirtualna Polska
    "WP": "WPL", "WIRTUALNA POLSKA": "WPL",
    # Comp
    "COMP": "CMP",
    # Asbis
    "ASBIS": "ASB",
}


def resolve_ticker(ticker: str) -> str:
    """Normalizuje nazwę AI do symbolu GPW."""
    return _GPW_MAP.get(ticker.upper().strip(), ticker.upper().strip())


# ── Źródła cen ───────────────────────────────────────────────────────────────

def _try_yfinance(symbol: str) -> float | None:
    import yfinance as yf
    for sym in [f"{symbol}.WA", symbol]:
        try:
            price = getattr(yf.Ticker(sym).fast_info, "last_price", None)
            if price and float(price) > 0:
                return float(price)
        except Exception:
            continue
    return None


def _try_stooq(symbol: str) -> float | None:
    """Pobiera kurs z stooq.pl przez HTTP. Działa dla GPW."""
    sym_lower = symbol.lower()
    url = f"https://stooq.com/q/l/?s={sym_lower}.pl&f=sd2t2ohlcv&e=csv"
    try:
        r = httpx.get(url, timeout=8.0, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        lines = [ln.strip() for ln in r.text.strip().splitlines() if ln.strip()]
        # Oczekiwany format:
        # Symbol,Date,Time,Open,High,Low,Close,Volume
        # CDR.PL,2026-04-24,17:05:26,148.4,148.8,145.0,147.4,123456
        if len(lines) < 2:
            return None
        parts = lines[-1].split(",")
        if len(parts) < 7:
            return None
        close_str = parts[6].strip()
        if close_str in ("N/D", "", "Close"):
            return None
        price = float(close_str)
        return price if price > 0 else None
    except Exception:
        return None


# ── Publiczne API ─────────────────────────────────────────────────────────────

def get_share_price(ticker: str) -> tuple[float | None, str]:
    """
    Pobiera aktualny kurs waloru.
    Zwraca (cena, źródło) lub (None, '').

    Kolejność źródeł:
      1. yfinance  {symbol}.WA / {symbol}
      2. stooq.pl  {symbol}.pl
    """
    normalized = resolve_ticker(ticker)

    price = _try_yfinance(normalized)
    if price:
        logger.debug(f"💹 {ticker}→{normalized}: {price:.2f} PLN [yfinance]")
        return price, "yfinance"

    price = _try_stooq(normalized)
    if price:
        logger.debug(f"💹 {ticker}→{normalized}.pl: {price:.2f} PLN [stooq]")
        return price, "stooq"

    logger.warning(f"⚠️ Brak kursu dla '{ticker}' (normalizacja: '{normalized}')")
    return None, ""
