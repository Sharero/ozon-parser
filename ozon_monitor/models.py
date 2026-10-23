import re
from dataclasses import dataclass
from decimal import Decimal


PRICE_LIMIT = Decimal('550')


def normalized(value: str) -> str:
    return ' '.join(value.casefold().replace('ё', 'е').split())


def rubles(value: str) -> Decimal | None:
    matches = re.findall(r'(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*[₽]', value)
    if len(matches) != 1:
        return None
    return Decimal(re.sub(r'\s', '', matches[0]).replace(',', '.'))


@dataclass(frozen=True)
class Book:
    sku: str
    url: str
    title: str
    author: str
    series: str
    binding: str
    card_price: Decimal | None
    available: bool | None

    @property
    def matches(self) -> bool:
        author = normalized(self.author)
        return (
            bool(re.search(r'\bкинг\b', author))
            and bool(re.search(r'\bстивен\b', author))
            and normalized(self.series) == 'король на все времена'
            and normalized(self.binding) in {'твердый переплет', 'твердая обложка', 'твердый'}
        )

    @property
    def eligible(self) -> bool:
        return self.matches and self.available is True and self.card_price is not None and 0 < self.card_price < PRICE_LIMIT

    @property
    def rejection_reason(self) -> str:
        author = normalized(self.author)
        if not re.search(r'\bкинг\b', author) or not re.search(r'\bстивен\b', author):
            return 'другой автор'
        if normalized(self.series) != 'король на все времена':
            return 'другая серия'
        if not self.matches:
            return 'не подтверждён твёрдый переплёт'
        if self.available is not True:
            return 'нет подтверждённой доступности'
        if self.card_price is None:
            return 'нет цены с Ozon Картой'
        if not 0 < self.card_price < PRICE_LIMIT:
            return 'цена вне порога <550 ₽'
        return ''
