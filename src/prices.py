"""
Pobieranie aktualnych kursów akcji — stooq.pl (primary) + yfinance (fallback).

Kolejność dla każdego tickera:
  1. stooq.pl  → {symbol}.pl  (GPW — najlepsze pokrycie)
  2. yfinance  → {symbol}.WA  (GPW przez Yahoo Finance)
  3. yfinance  → {symbol}     (globalnie, np. CDR bez .WA)
"""

import difflib

import httpx
from loguru import logger


# ── Mapa: nazwa z AI → symbol GPW ────────────────────────────────────────────
# WIG20 + WIG40 + mWIG40 + popularne spółki + typowe skróty używane przez traderów.
# Format: "NAZWA_UPPERCASE": "TICKER"
_GPW_MAP: dict[str, str] = {
    # ── WIG20 ─────────────────────────────────────────────────────────────────
    "ALLEGRO": "ALE",
    "CDPROJEKT": "CDR", "CD PROJEKT": "CDR", "CDPROJEKT RED": "CDR", "CDPR": "CDR", "CD PROJECT": "CDR",
    "CYFRPLSAT": "CPS", "CYFROWY POLSAT": "CPS", "CYFROWYPOLSAT": "CPS",
    "POLSAT": "CPS",  # skrót Damiana
    "DINO": "DNP", "DINO POLSKA": "DNP",
    "JSWIENNA": "JSW", "JASTRZEBSKA": "JSW", "JASTRZĘBSKA": "JSW",
    "KGHM": "KGH", "KGHM POLSKA MIEDZ": "KGH", "KGHM POLSKA MIEDŹ": "KGH", "KGHM POLSKA": "KGH",
    "MIEDZ": "KGH", "MIEDŹ": "KGH",
    "LPPSA": "LPP",
    "MBANK": "MBK",
    "ORANGEPL": "OPL", "ORANGE": "OPL", "ORANGE POLSKA": "OPL",
    "PEKAO": "PEO", "BANK PEKAO": "PEO", "SA PEKAO": "PEO",
    "PGE": "PGE", "PGENERGIA": "PGE", "POLSKA GRUPA ENERGETYCZNA": "PGE",
    "PGNIG": "PGN", "PGN": "PGN",
    "PKOBP": "PKO", "PKO BP": "PKO", "PKO BANK POLSKI": "PKO",
    "PKN ORLEN": "PKN", "PKNORLEN": "PKN", "ORLEN": "PKN", "PKN": "PKN",
    "PZUSA": "PZU", "PZU": "PZU",
    "SANTANDER": "SPL", "SANTANDER BANK": "SPL", "BZ WBK": "SPL",
    "TAURON": "TPE", "TAURON POLSKA ENERGIA": "TPE",
    "XTB": "XTB",

    # ── WIG40 / mWIG40 ────────────────────────────────────────────────────────
    "ASSECO": "ACP", "ASSECO POLAND": "ACP",
    "BUDIMEX": "BDX",
    "BENEFIT": "BFT", "BENEFIT SYSTEMS": "BFT",
    "CCCSA": "CCC", "CCC": "CCC",
    "COLIAN": "COL",
    "COMARCH": "CMR",
    "COMP": "CMP",
    "CREATIVECOMMONS": "CRE",
    "CREOTECH": "CRQ",
    "DATATALK": "DAT",
    "DOMDEV": "DOM", "DOM DEVELOPMENT": "DOM",
    "ECHO": "ECH", "ECHO INVESTMENT": "ECH",
    "ENTER": "ENA", "ENTER AIR": "ENA",
    "AMREST": "EAT",
    "FERRATUM": "FRR",
    "GLOBALWORTH": "GTC", "GTC": "GTC",
    "HUUUGE": "HUG",
    "INDOSAD": "IND",
    "ING": "ING", "ING BANK SLASKI": "ING", "ING BANK ŚLĄSKI": "ING",
    "KCYF": "KCY",
    "LIVECHAT": "LVC", "LIVE CHAT": "LVC",
    "LENTEX": "LTX",
    "MERCATOR": "MRC", "MERCATOR MEDICAL": "MRC",
    "MILLENNIUM": "MIL", "MILLENIUM": "MIL", "BANK MILLENNIUM": "MIL",
    "MORIZON": "MRZ",
    "NETIA": "NET",
    "NOVAVIS": "NVT",
    "OPONEO": "OPN",
    "PEPCO": "PCO",
    "PHARMENA": "PHR",
    "PLAYWAY": "PLY",
    "POLENERGIA": "PEP",
    "POLIMEXMS": "PXM", "POLIMEX": "PXM",
    "ROPCZYCE": "RPC",
    "SERINUS": "SEN",
    "STALPRODUKT": "STL",
    "SYNEKTIK": "SNT",
    "TEN SQUARE": "TEN", "TENSQUARE": "TEN", "TEN SQUARE GAMES": "TEN",
    "TORPOL": "TOR",
    "UNIDEV": "UNI",
    "UNIMOT": "UNT",
    "VERCOM": "VRC",
    "VRG": "VRG",
    "WIRTUALNA POLSKA": "WPL", "WP": "WPL",
    "WIELTON": "WLT",
    "WITTCHEN": "WTN",
    "11BIT": "11B", "11 BIT": "11B", "11 BIT STUDIOS": "11B",
    "ASBIS": "ASB",
    "BEST": "BST",

    # ── Inne popularne / często wymieniane ────────────────────────────────────
    "ARCTIC": "ATC",
    "ATM": "ATM",
    "BANK HANDLOWY": "BHW", "HANDLOWY": "BHW", "CITIBANK": "BHW",
    "BIOMED": "BMD",
    "BOGDANKA": "LWB", "LWB": "LWB",
    "BORYSZEW": "BRS",
    "CAPITAL PARK": "CAP",
    "CALATRAVA": "CAT",
    "CELON PHARMA": "CEL",
    "COGNOR": "COG",
    "ELEMENTAL": "ELM",
    "EMC INSTYTUT": "EMC",
    "ENERGA": "ENG",
    "ENEA": "ENA",  # uwaga: ENA to też Enter Air — Energa jest ENA na GPW
    "FERRO": "FRO",
    "GETBACK": "GBK",
    "GPWSA": "GPW", "GIEŁDA PAPIERÓW WARTOŚCIOWYCH": "GPW",
    "INTERCARS": "CAR", "INTER CARS": "CAR",
    "KERNEL": "KER",
    "KRUK": "KRU",
    "LENA LIGHTING": "LEN",
    "LIBET": "LBT",
    "LOKUM": "LKD",
    "MENNICA": "MNC", "MENNICA POLSKA": "MNC",
    "MIRBUD": "MRB",
    "MONNARI": "MON",
    "NEUCA": "NEU",
    "NORTFACE": "NFC",
    "NOVABEV": "NBV",
    "OPONEO.PL": "OPN",
    "ORZBIAŁY": "ORB",
    "PBKM": "PBK",
    "PCGUARD": "PCG",
    "PMPG POLSKIE MEDIA": "PMP",
    "POLNORD": "PND",
    "RAINBOW": "RBW",
    "RONSON": "RON",
    "SANOK RUBBER": "SAN",
    "SECOMGROUP": "SEC",
    "SILVAIR": "SLV",
    "SIMPLE": "SME",
    "SKOTAN": "SKT",
    "SOLAR": "SOL",
    "SONEL": "SNL",
    "SWISSMED": "SWM",
    "SYGNITY": "SGN",
    "URSUS": "URS",
    "VIGO PHOTONICS": "VGO", "VIGO": "VGO",
    "VISTAL": "VIS",
    "WOJAS": "WOJ",
    "WORK SERVICE": "WKS",
    "ZETKAMA": "ZKA",
    "ZREMB": "ZRE",
}


def resolve_ticker(ticker: str) -> str:
    """Normalizuje nazwę AI do symbolu GPW. Najpierw dokładne dopasowanie, potem fuzzy."""
    key = ticker.upper().strip()
    if key in _GPW_MAP:
        return _GPW_MAP[key]
    # Jeśli to już krótki ticker (≤5 znaków, same litery/cyfry) — nie rób fuzzy
    if len(key) <= 5 and key.replace("1", "").isalpha():
        return key
    # Fuzzy match — obsługuje literówki i drobne różnice nazw spółek
    return _fuzzy_resolve(key) or key


def _fuzzy_resolve(name: str) -> str | None:
    """Szuka najbardziej podobnego klucza w _GPW_MAP (próg 0.78)."""
    keys = list(_GPW_MAP.keys())
    matches = difflib.get_close_matches(name, keys, n=1, cutoff=0.78)
    if matches:
        ticker = _GPW_MAP[matches[0]]
        logger.debug(f"🔍 Fuzzy: '{name}' → '{matches[0]}' → {ticker}")
        return ticker
    return None


# ── Źródła cen ───────────────────────────────────────────────────────────────

def _try_stooq(symbol: str) -> float | None:
    """Pobiera kurs z stooq.pl. Najlepsze pokrycie dla GPW."""
    sym_lower = symbol.lower()
    url = f"https://stooq.com/q/l/?s={sym_lower}.pl&f=sd2t2ohlcv&e=csv"
    try:
        r = httpx.get(url, timeout=8.0, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        lines = [ln.strip() for ln in r.text.strip().splitlines() if ln.strip()]
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


# ── Publiczne API ─────────────────────────────────────────────────────────────

def get_share_price(ticker: str) -> tuple[float | None, str]:
    """
    Pobiera aktualny kurs waloru.
    Zwraca (cena, źródło) lub (None, '').

    Kolejność źródeł:
      1. stooq.pl  {symbol}.pl  ← primary (lepsze pokrycie GPW)
      2. yfinance  {symbol}.WA / {symbol}
    """
    normalized = resolve_ticker(ticker)

    price = _try_stooq(normalized)
    if price:
        logger.debug(f"💹 {ticker}→{normalized}: {price:.2f} PLN [stooq]")
        return price, "stooq"

    price = _try_yfinance(normalized)
    if price:
        logger.debug(f"💹 {ticker}→{normalized}: {price:.2f} PLN [yfinance]")
        return price, "yfinance"

    logger.warning(f"⚠️ Brak kursu dla '{ticker}' (normalizacja: '{normalized}')")
    return None, ""
