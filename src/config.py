"""
Centralna konfiguracja projektu — ładowanie zmiennych z .env.

Użycie:
    from src.config import settings
    print(settings.telegram_api_id)
"""

from pathlib import Path
from pydantic_settings import BaseSettings


# Root projektu — katalog nadrzędny src/
PROJECT_ROOT = Path(__file__).parent.parent

# Ścieżki do katalogów danych
DB_DIR = PROJECT_ROOT / "db"
MEDIA_DIR = PROJECT_ROOT / "media"
LOGS_DIR = PROJECT_ROOT / "logs"


class Settings(BaseSettings):
    """Konfiguracja aplikacji z .env"""

    # --- Telegram API (userbot — Telethon) ---
    telegram_api_id: int = 0
    telegram_api_hash: str = ""

    # --- Telegram Bot (Decision Bot) ---
    bot_token: str = ""

    # --- Channel / Group IDs ---
    source_group_id: int = 0        # Kanał/grupa tradera (źródło)
    raw_channel_id: int = 0         # Prywatny kanał archiwum (opcjonalne)
    decision_chat_id: int = 0       # Twoje chat ID

    # --- AI ---
    gemini_api_key: str = ""
    anthropic_api_key: str = ""   # Claude Haiku fallback
    openai_api_key: str = ""      # GPT-4o-mini fallback

    # --- Portfolio ---
    my_portfolio_size: float = 100_000.0    # PLN
    signal_ttl_minutes: int = 15            # auto-EXPIRED po tym czasie

    # --- Prywatna grupa Damiana (forum topics) ---
    damian_group_id: int = 0
    damian_ike_topic_id: int = 8951
    damian_ikze_topic_id: int = 8953
    damian_session_name: str = "damian_watcher"
    userbot_phone: str = ""          # numer tel. do logowania Telethon

    # --- Ustawienia techniczne ---
    session_name: str = "signal_copier"     # nazwa pliku .session
    db_path: str = str(DB_DIR / "signals.db")
    media_group_wait_seconds: float = 1.5   # bufor na media_group

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton — importuj to
settings = Settings()


def ensure_directories():
    """Tworzy katalogi danych jeśli nie istnieją."""
    for dir_path in [DB_DIR, MEDIA_DIR, LOGS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)
