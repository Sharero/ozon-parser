"""Series discovery and book validation over the upstream client's JSON transport."""
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from .models import Book
from .ozon import SEARCHES
from .price_json import card_offer
from .search_config import search_urls, page_url
from .selenium_dom import DOMModeSelected, SeleniumDOMOzon

log = logging.getLogger(__name__)
ORIGIN = 'https://www.ozon.ru'


def states(payload):
    result = {}
    for key, raw in payload.get('widgetStates', {}).items():
        try:
            state = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if isinstance(state, dict):
            result[key] = state
    if not result:
        raise RuntimeError('JSON Ozon не содержит распознанных виджетов')
    return result


def walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def product_url(value):
    if not isinstance(value, str):
        return None
    parts = urlsplit(urljoin(ORIGIN, value))
    if parts.hostname not in ('ozon.ru', 'www.ozon.ru'):
        return None
    match = re.fullmatch(r'/product/(?:[^/]*-)?(\d+)/?', parts.path)
    return (match[1], ORIGIN + parts.path.rstrip('/') + '/') if match else None


def search_page(payload, page_number):
    widgets = states(payload)
    results = [state for key, state in widgets.items() if key.startswith('searchResultsV2-')]
    if not results:
        raise RuntimeError('JSON поиска не содержит searchResultsV2; выдача не подтверждена')
    links = {}
    for result in results:
        if not isinstance(result.get('items'), list):
            raise RuntimeError('Не распознана структура items поисковой выдачи')
        for item in result['items']:
            for node in walk(item):
                # Only product actions, not arbitrary recommendation text.
                if isinstance(node, dict) and isinstance(node.get('link'), str):
                    parsed = product_url(node['link'])
                    if parsed:
                        links[parsed[0]] = parsed[1]
    next_pages = []
    pagination_recognized = False
    for key, state in widgets.items():
        if not key.startswith(('megaPaginator-', 'searchResultsV2-')):
            continue
        for node in walk(state):
            if not isinstance(node, dict):
                continue
            for field in ('nextPage', 'nextPageLink', 'nextLink', 'next'):
                if field not in node:
                    continue
                pagination_recognized = True
                value = node.get(field)
                if isinstance(value, dict):
                    value = value.get('link') or value.get('url')
                if type(value) is int or (isinstance(value, str) and value.isdigit()):
                    number = int(value)
                elif isinstance(value, str):
                    try:
                        number = int(parse_qs(urlsplit(value).query).get('page', ['0'])[0])
                    except ValueError:
                        continue
                else:
                    continue
                if number > page_number:
                    next_pages.append(number)
    if links and not pagination_recognized:
        raise RuntimeError('JSON не подтвердил пагинацию; полный обход серии не гарантирован')
    return links, min(next_pages) if next_pages else None


def text_value(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ', '.join(filter(None, (text_value(child) for child in value)))
    if isinstance(value, dict):
        return text_value(value.get('text') or value.get('title') or value.get('value') or '')
    return ''


def characteristics(widgets):
    labels = {'Автор', 'Авторы', 'Серия', 'Тип обложки', 'Переплет', 'Переплёт'}
    found = {}
    for key, state in widgets.items():
        if not key.startswith(('webCharacteristics-', 'webShortCharacteristics-', 'webProductCharacteristics-')):
            continue
        for node in walk(state):
            if not isinstance(node, dict):
                continue
            label = text_value(node.get('name') or node.get('title') or node.get('key') or '')
            if label not in labels:
                continue
            value = text_value(node.get('values') or node.get('value') or node.get('text') or '')
            if value:
                found.setdefault(label, set()).add(value)
    if any(len(values) > 1 for values in found.values()):
        raise RuntimeError('Противоречивые характеристики книги в JSON')
    return {key: next(iter(values)) for key, values in found.items()}


class UpstreamOzon:
    def __init__(self, directory, max_pages=100, delay=3, transport=None, search_url=None, kind='series', strict_metadata=True):
        if transport is None:
            from .upstream_transport import UpstreamTransport
            transport = UpstreamTransport(directory, probe_url=search_url, probe_kind=kind)
        self.transport, self.max_pages, self.delay = transport, max_pages, delay
        self.directory = directory
        self.search_url, self.kind, self.strict_metadata = search_url, kind, strict_metadata and kind != 'product'

    def close(self):
        self.transport.close()

    def login(self, setup_url='https://www.ozon.ru/'):
        self.transport.refresh(interactive=True, setup_url=setup_url)

    def fetch(self, url):
        time.sleep(self.delay)
        return self.transport.page(url)

    def _dom_source(self):
        if self.transport.driver is None:
            raise RuntimeError('HTML-режим требует открытого Selenium')
        source = SeleniumDOMOzon(self.transport.driver, self.max_pages, self.delay)
        source.diagnostic_directory = self.directory / 'diagnostics'
        source.search_url = self.search_url
        source.strict_metadata = self.strict_metadata
        return source

    def inspect_price(self, url):
        parsed = product_url(url)
        if not parsed:
            raise ValueError('Для --inspect нужна ссылка на конкретную книгу Ozon')
        if self.transport.driver is None:
            self.transport.refresh(retain_browser=True)
        # The JSON path may work without retaining a browser. Inspection needs a visible DOM.
        if self.transport.driver is None:
            raise RuntimeError('Для диагностики требуется открытый Selenium; текущий JSON-режим закрыл браузер')
        return self._dom_source().inspect_price(parsed[1])

    def discover(self):
        if self.kind in ('book', 'product'):
            return [self.search_url]
        if self.transport.mode == 'dom':
            return self._dom_source().discover()
        try:
            return self._discover_json()
        except DOMModeSelected:
            return self._dom_source().discover()

    def _discover_json(self):
        self.transport.refresh_attempted = False
        all_links = {}
        for base_url in search_urls(SEARCHES, self.search_url):
            log.info('Выдача: %s', base_url)
            number = 1
            seen = set()
            for _ in range(self.max_pages):
                payload = self.fetch(page_url(base_url, number))
                if not any(key.startswith('searchResultsV2-') for key in states(payload)):
                    log.info('JSON поиска без searchResultsV2; проверяю обычную HTML-выдачу')
                    self.transport.select_dom(base_url)
                    raise DOMModeSelected()
                links, next_page = search_page(payload, number)
                if links and not links.keys() - seen:
                    raise RuntimeError('JSON Ozon повторил страницу поиска; обход не завершён')
                seen.update(links)
                all_links.update(links)
                if next_page is None:
                    break
                number = next_page
            else:
                raise RuntimeError('Достигнут MAX_SEARCH_PAGES; обход не завершён')
        return list(all_links.values())

    def book(self, url):
        if self.transport.mode == 'dom':
            return self._dom_source().book(url)
        try:
            return self._book_json(url)
        except DOMModeSelected:
            return self._dom_source().book(url)

    def _book_json(self, url):
        parsed = product_url(url)
        if not parsed:
            raise RuntimeError('Некорректный URL книги Ozon')
        sku, canonical_url = parsed
        payload = self.fetch(canonical_url)
        widgets = states(payload)
        fields = {} if self.kind == 'product' else characteristics(widgets)
        title = next((state.get('title') for key, state in widgets.items()
                      if key.startswith('webProductHeading-') and isinstance(state.get('title'), str)), '')
        author = fields.get('Автор') or fields.get('Авторы', '')
        series = fields.get('Серия', '')
        binding = fields.get('Тип обложки') or fields.get('Переплет') or fields.get('Переплёт', '')
        if not title:
            raise RuntimeError('JSON не подтвердил название книги')
        if self.strict_metadata and not all((author, series, binding)):
            raise RuntimeError('JSON не подтвердил название, автора, серию или переплёт; уведомление не отправлено')
        offer = card_offer(payload, sku)
        if offer is None:
            raise RuntimeError('JSON не подтвердил цену с Ozon Картой для нужного SKU')
        price, available = offer
        book = Book(sku, canonical_url, title, author, series, binding, price, available)
        if available and price is None:
            raise RuntimeError('JSON не содержит распознанной цены cardPrice')
        return book
