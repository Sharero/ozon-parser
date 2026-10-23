Source: https://github.com/noobby91/ozon-price-tracker-bot (main)
Retrieved: 2026-10-21 via raw.githubusercontent.com.
Copied: ozon_client.py, price_checker.py, LICENSE (MIT, Copyright 2026 Alexei).
Local changes: relative price_checker import; removed module-level singleton/get_ozon_price
to avoid runtime state in the source directory. Other bot/UI modules were not imported.
The active adapter subclasses OzonClient and replaces its cookie acquisition/storage,
while reusing its HTTP transport, headers, endpoint construction and widget decoding.
