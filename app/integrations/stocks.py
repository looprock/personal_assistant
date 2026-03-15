"""
Stock price integration via yfinance.

Fetches the latest price and daily change for one or more tickers.
Tickers are configured in config.yaml (stocks.tickers) or PA_STOCKS_TICKERS env var.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from app.models import StockData

# yfinance is synchronous — run in a thread pool to avoid blocking the event loop
_executor = ThreadPoolExecutor(max_workers=4)


def _fetch_ticker_sync(ticker: str) -> StockData:
    t = yf.Ticker(ticker)
    info = t.fast_info

    price = float(info.last_price or 0)
    prev_close = float(info.previous_close or price)
    change = round(price - prev_close, 4)
    change_pct = round((change / prev_close * 100) if prev_close else 0, 2)

    return StockData(
        ticker=ticker.upper(),
        name=t.info.get("shortName") or t.info.get("longName"),
        price=round(price, 4),
        change=change,
        change_pct=change_pct,
        currency=info.currency or "USD",
    )


async def fetch(tickers: list[str]) -> list[StockData]:
    """Fetch current price data for all configured tickers concurrently."""
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_ticker_sync, ticker)
        for ticker in tickers
    ]
    return list(await asyncio.gather(*tasks))
