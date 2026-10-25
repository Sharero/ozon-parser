"""Persistent watch targets and evaluation independent of a particular author/series."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .models import Book, normalized


DEFAULT_SEARCH_URL = 'https://www.ozon.ru/series/korol-na-vse-vremena-21102932/category/knigi-16500/?covertype=537&sorting=price'


def price_limit(value: str) -> Decimal:
    try:
        amount = Decimal(value.strip().replace(',', '.'))
    except (InvalidOperation, AttributeError):
        raise ValueError('Укажите положительную цену в рублях, например 400 или 399.90') from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal('10000000') or amount.as_tuple().exponent < -2:
        raise ValueError('Цена должна быть от 0.01 до 10000000 ₽, не более двух знаков после запятой')
    return amount


def watch_url(value: str, kind: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme != 'https' or parts.hostname not in ('ozon.ru', 'www.ozon.ru') or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Нужна полная HTTPS-ссылка на ozon.ru без логина и пароля')
    if kind in ('book', 'product'):
        if not re.fullmatch(r'/product/(?:[^/]*-)?\d+/?', parts.path):
            raise ValueError('Нужна полная ссылка /product/… на конкретный товар, не короткая /t/ ссылка')
        return urlunsplit(('https', 'www.ozon.ru', parts.path.rstrip('/') + '/', '', ''))
    parameters = parse_qsl(parts.query)
    search_path = parts.path.rstrip('/') == '/search' or parts.path.startswith('/category/')
    search_text = any(key == 'text' and value.strip() for key, value in parameters)
    if kind != 'series' or not (parts.path.startswith('/series/') or (search_path and search_text)):
        raise ValueError('Для серии нужна ссылка /series/… либо /search/ или /category/… с поисковым запросом text')
    query = [(k, v) for k, v in parameters if k not in ('page', 'sorting', 'covertype') and not k.startswith('utm_')]
    query.extend([('covertype', '537'), ('sorting', 'price')])
    return urlunsplit(('https', 'www.ozon.ru', parts.path, urlencode(query), ''))


@dataclass(frozen=True)
class Target:
    id: int
    kind: str
    url: str
    name: str
    series: str = ''
    author: str = ''
    hardcover: bool = True
    enabled: bool = True
    price_limit: Decimal = Decimal('550')
    price_revision: int = 0

    @property
    def sorted_by_price(self):
        return self.kind == 'series' and dict(parse_qsl(urlsplit(self.url).query)).get('sorting') == 'price'

    def evaluate(self, book: Book, limit: Decimal) -> tuple[bool, str, bool]:
        """(eligible, reason, unknown). Unknown evidence must not reset notification state."""
        if self.author:
            if not book.author:
                return False, 'Не подтверждён автор', True
            expected = set(re.findall(r'\w+', normalized(self.author)))
            if not expected.issubset(set(re.findall(r'\w+', normalized(book.author)))):
                return False, 'Другой автор', False
        if self.series:
            if not book.series:
                return False, 'Не подтверждена серия', True
            if normalized(book.series) != normalized(self.series):
                return False, 'Другая серия', False
        if self.hardcover:
            if not book.binding:
                return False, 'Не подтверждён переплёт', True
            if normalized(book.binding) not in {'твердый переплет', 'твердая обложка', 'твердый', 'твердый (7бц)'}:
                return False, 'Не твёрдый переплёт', False
        if book.available is None:
            return False, 'Не подтверждена доступность', True
        if not book.available:
            return False, 'Нет в наличии', False
        if book.card_price is None:
            return False, 'Не подтверждена цена Ozon Банка', True
        if not 0 < book.card_price < limit:
            return False, f'Цена вне порога <{limit} ₽', False
        return True, 'Подходит', False
