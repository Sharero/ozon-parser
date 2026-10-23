"""Read only explicitly identified Ozon Card prices for the requested SKU."""
import json
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from .models import rubles


def card_offer(payload: dict, sku: str) -> tuple[Decimal | None, bool] | None:
    widgets = payload.get('widgetStates')
    if not isinstance(widgets, dict):
        return None
    offers = set()
    for key, raw in widgets.items():
        if not key.startswith('webPrice-'):
            continue
        try:
            state = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(state, dict):
            continue
        link = state.get('link')
        if not isinstance(link, str) or parse_qs(urlsplit(link).query).get('product_id') != [sku]:
            continue
        lexemes = state.get('lexemes')
        if not isinstance(lexemes, dict) or 'withOzonCard' not in lexemes:
            continue
        if type(state.get('isAvailable')) is not bool:
            continue
        value = state.get('cardPrice')
        price = rubles(value) if isinstance(value, str) else None
        # Never replace cardPrice with price/oldPrice; unknown prices stay unknown.
        offers.add((price, state['isAvailable']))
    if len(offers) > 1:
        raise RuntimeError('JSON Ozon содержит противоречивые цены для одного SKU')
    return next(iter(offers)) if offers else None
