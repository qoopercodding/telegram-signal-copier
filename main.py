"""
Punkt startowy aplikacji.
Uruchomienie: python main.py
"""

import asyncio
from src.listener import main

if __name__ == "__main__":
    asyncio.run(main())
