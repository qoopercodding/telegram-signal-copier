"""
Punkt startowy Monitor Bota.
Uruchomienie: python run_monitor.py
"""

import asyncio
from src.monitor_bot import main

if __name__ == "__main__":
    asyncio.run(main())
