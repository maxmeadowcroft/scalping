"""Target drop monitoring and checkout automation.

Package layout
--------------
- config.py          — configuration.json + .env payment loading
- runtime.py         — ~/.scalping data dirs / Chrome profiles
- target_stock.py    — PDP stock detection + add to cart
- target_checkout.py — cart, fulfillment, dry-run / place order
- target_bot.py      — poll loop CLI (python -m scraping / scraping.target_bot)
"""

from scraping.config import AppConfig, ItemConfig, load_config

__all__ = ["AppConfig", "ItemConfig", "load_config"]
