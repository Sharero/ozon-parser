import logging
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit
from .models import Book, rubles
from .diagnostics import ozon_access_error, check_age_confirmation
from .price_json import card_offer
from .search_dom import SEARCH_SNAPSHOT, validate_search, describe_search
from .search_config import search_urls, page_url
from .price_diagnostic import PRICE_DIAGNOSTIC, save_diagnostic
from .bank_prices import OPEN_BANK_DETAILS, READ_BANK_DETAILS, CLOSE_BANK_DETAILS, select_ozon_price

log = logging.getLogger(__name__)
SEARCHES = ('Стивен Кинг Король на все времена', 'Кинг Король на все времена твердый')

# Use semantic labels instead of generated CSS classes. Unknown markup fails closed.
EXTRACT = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const fields = {};
  const labels = new Set(['Автор', 'Авторы', 'Серия', 'Тип обложки', 'Переплет', 'Переплёт', 'Тип переплета', 'Тип переплёта', 'Обложка']);
  const aliases = {'Тип переплета': 'Тип обложки', 'Тип переплёта': 'Тип обложки', 'Переплет': 'Тип обложки', 'Переплёт': 'Тип обложки', 'Обложка': 'Тип обложки'};
  const characteristicRoots = [...document.querySelectorAll('[data-widget*="Characteristics"], #section-characteristics')];
  const inCharacteristics = el => characteristicRoots.some(root => root.contains(el));
  const priority = {};
  const store = (label, value, el) => {
    const key = aliases[label] || label;
    value = clean(value);
    if (!value || value.length >= 500 || labels.has(value)) return;
    if (key === 'Тип обложки' && /мягк/i.test(value) && /тв[её]рд/i.test(value)) return;
    const rank = inCharacteristics(el) ? 2 : 1;
    if (!priority[key] || rank > priority[key]) { fields[key] = value; priority[key] = rank; }
  };
  for (const el of document.querySelectorAll('dt, span, div, th, td')) {
    const key = clean(el.innerText).replace(/:$/, '');
    if (!labels.has(key)) continue;
    let value = '';
    if (el.tagName === 'DT') value = el.nextElementSibling?.innerText;
    if (!value && el.nextElementSibling) value = el.nextElementSibling.innerText;
    if (!value) {
      const row = el.parentElement?.parentElement;
      const text = clean(row?.innerText);
      if (text?.startsWith(key)) value = text.slice(key.length).replace(/^\s*:\s*/, '');
    }
    store(key, value, el);
  }
  // Variant choices are evidence only when explicitly selected, never from a list of all covers.
  if (!fields['Тип обложки'] && !fields['Переплет'] && !fields['Переплёт']) {
    const selected = new Set();
    for (const el of document.querySelectorAll('[aria-checked="true"], [aria-selected="true"], input:checked')) {
      const text = clean(el.innerText || el.closest('label')?.innerText);
      if (/^(твердый переплет|твёрдый переплёт|мягкая обложка|твердая обложка|твёрдая обложка)$/i.test(text)) selected.add(text);
    }
    if (selected.size === 1) fields['Тип обложки'] = [...selected][0];
  }
  const prices = new Set();
  const bankGroupPrices = new Set();
  const root = document.querySelector('[data-widget="webPrice"]');
  if (root) {
    for (const el of root.querySelectorAll('*')) {
      const label = clean(el.innerText);
      const group = /^с банками$/i.test(label);
      if (!group && !/^(?:[сc]\s+)?(?:ozon|озон)[ -]?карт(?:ой|а)$/i.test(label)) continue;
      let node = el;
      for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
        if (!root.contains(node)) break;
        const text = clean(node.innerText);
        const found = text.match(/\d[\d\s]*(?:[.,]\d{1,2})?\s*₽/g) || [];
        if (found.length === 1 && !/без\s+(?:ozon|озон)?\s*карт/i.test(text)) {
          (group ? bankGroupPrices : prices).add(found[0]); break;
        }
        if (found.length > 1) break;
      }
    }
  }
  const buyRoot = document.querySelector('[data-widget="webAddToCart"], [data-widget="webSale"]');
  const stockText = clean(buyRoot?.innerText);
  const cart = [...document.querySelectorAll('[data-widget="webAddToCart"] button, [data-widget="webAddToCart"] [role="button"], [data-widget="webAddToCart"] a, [data-widget="webSale"] button')]
    .some(el => /^(?:(?:добавить\s+)?в корзину|купить сейчас|купить в один клик)(?:$|\s)/i.test(clean(el.innerText)) &&
      !el.disabled && el.getAttribute('aria-disabled') !== 'true' && el.getClientRects().length);
  return {title: clean(document.querySelector('h1')?.innerText), fields,
    price: prices.size === 1 ? [...prices][0] : null,
    bank_group_price: bankGroupPrices.size === 1 ? [...bankGroupPrices][0] : null,
    available: cart ? true : (/товар закончился|нет в наличии|нет доступных предложений/i.test(stockText) ? false : null)};
}"""


EXPAND_CHARACTERISTICS = r"""() => {
  const section = document.querySelector('#section-characteristics, [data-widget="webCharacteristics"]');
  if (section) {
    section.scrollIntoView({block: 'start'});
    const expand = [...section.querySelectorAll('button, a, [role="button"]')].find(el =>
      /^(все характеристики|показать все|развернуть)$/i.test((el.innerText || '').trim()));
    if (expand) expand.click();
    return;
  }
  const link = [...document.querySelectorAll('a, button')].find(el =>
    /^(все характеристики|перейти к характеристикам)$/i.test((el.innerText || '').trim()) ||
    (el.tagName === 'A' && (el.getAttribute('href') || '').startsWith('#section-characteristics')));
  if (link) link.click();
}"""


class Ozon:
    def __init__(self, page, max_pages: int = 100, delay: float = 3):
        self.page, self.max_pages, self.delay = page, max_pages, delay
        self.warmed_up = False
        self.diagnostic_directory = None
        self.last_price_diagnostic = {}
        self.search_url = None
        self.strict_metadata = True

    def goto(self, url: str):
        time.sleep(self.delay)
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
        self.page.wait_for_timeout(2000)
        text = self.page.locator('body').inner_text()
        check_age_confirmation(text)
        error = ozon_access_error(text, response.status if response else 200)
        if error and (not response or response.status in (200, 403)):
            log.info('Ожидаю завершения проверки Ozon (до 20 секунд)')
            for _ in range(20):
                self.page.wait_for_timeout(1000)
                text = self.page.locator('body').inner_text()
                # Do not declare success from cookie presence or page title alone.
                content = self.page.locator('[data-widget="searchResultsV2"], [data-widget="webProductHeading"], [data-widget="header"]').count()
                error = ozon_access_error(text, 200 if content else (response.status if response else 200))
                if not error:
                    break
        if error:
            self.warmed_up = False
            raise RuntimeError(error)

    def search_snapshot(self):
        return self.page.evaluate(SEARCH_SNAPSHOT)

    def wait_search(self, timeout=30):
        snapshot = {}
        for _ in range(max(1, int(timeout * 2))):
            snapshot = self.search_snapshot()
            if validate_search(snapshot):
                return snapshot
            self.page.wait_for_timeout(500)
        raise RuntimeError('Не распознаны карточки HTML-поиска: ' + describe_search(snapshot))

    def discover(self) -> list[str]:
        if not self.warmed_up:
            self.goto('https://www.ozon.ru/')
            self.warmed_up = True
        all_links = {}
        for base_url in search_urls(SEARCHES, self.search_url):
            log.info('Выдача: %s', base_url)
            previous = set()
            for number in range(1, self.max_pages + 1):
                self.goto(page_url(base_url, number))
                self.wait_search()
                for _ in range(4):
                    self.page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    self.page.wait_for_timeout(750)
                snapshot = self.search_snapshot()
                validate_search(snapshot)
                hrefs = snapshot['links']
                current = {}
                for href in hrefs:
                    url = urljoin('https://www.ozon.ru', href)
                    parts = urlsplit(url)
                    match = re.fullmatch(r'/product/(?:[^/]*-)?(\d+)/?', parts.path)
                    if parts.hostname in {'www.ozon.ru', 'ozon.ru'} and match:
                        current[match[1]] = 'https://www.ozon.ru' + parts.path
                if not current:
                    if snapshot.get('empty'):
                        break
                    raise RuntimeError('HTML-поиск не содержит распознанных ссылок: ' + describe_search(snapshot))
                if not (current.keys() - previous):
                    raise RuntimeError('Ozon повторил страницу выдачи; обход не завершен')
                previous.update(current)
                all_links.update(current)
                has_next = any(
                    int(value) > number
                    for href in snapshot.get('next_links', [])
                    for value in re.findall(r'[?&]page=(\d+)', href)
                )
                if not has_next and not snapshot.get('next_button'):
                    break
            else:
                raise RuntimeError('Достигнут MAX_SEARCH_PAGES; увеличьте лимит для полного обхода')
        return list(all_links.values())

    def json_offer(self, url: str, sku: str):
        # Browser fetch shares both cookies and the browser's network stack.
        path = urlsplit(url).path
        endpoint = '/api/entrypoint-api.bx/page/json/v2?' + urlencode({'url': path})
        try:
            result = self.page.evaluate("""async endpoint => {
                const abort = new AbortController();
                const timer = setTimeout(() => abort.abort(), 20000);
                try {
                    const response = await fetch(endpoint, {credentials: 'same-origin',
                        headers: {'Accept': 'application/json', 'x-o3-app-name': 'dweb_client'}, signal: abort.signal});
                    return {status: response.status, text: await response.text()};
                } finally { clearTimeout(timer); }
            }""", endpoint)
            if result['status'] != 200:
                log.warning('JSON цены недоступен (HTTP %s); проверяю видимый блок цены', result['status'])
                return None
            import json
            payload = json.loads(result['text'])
            if not isinstance(payload, dict) or 'challengeURL' in payload or 'incidentId' in payload:
                return None
        except Exception:
            log.warning('JSON цены не получен; проверяю видимый блок цены')
            return None
        return card_offer(payload, sku)

    def read_bank_price(self, group_price=None):
        if self.page.evaluate(OPEN_BANK_DETAILS) is not True:
            return None
        details = {}
        try:
            for _ in range(6):
                self.page.wait_for_timeout(500)
                details = self.page.evaluate(READ_BANK_DETAILS)
                if details.get('rows') or details.get('membership'):
                    break
            self.last_price_diagnostic['bank_details'] = details
            for snippet in details.get('snippets', [])[:2]:
                log.info('Список банков: %s', snippet.get('text', '')[:1200])
            price = select_ozon_price(details, group_price)
            if price is not None:
                log.info('Цена Ozon Банка подтверждена в списке банков: %s ₽', price)
            else:
                log.warning('Список банков открыт, но отдельная цена Ozon Банка не подтверждена')
            return price
        finally:
            self.page.evaluate(CLOSE_BANK_DETAILS)

    def book(self, url: str) -> Book:
        self.goto(url)
        self.page.wait_for_selector('h1', timeout=30000)
        early_data = self.page.evaluate(EXTRACT)
        self.last_price_diagnostic = {'before_scroll': self.page.evaluate(PRICE_DIAGNOSTIC)}
        bank_price = None
        if not early_data.get('price') and early_data.get('bank_group_price'):
            log.info('Общая цена «С банками»: %s; раскрываю предложение Ozon Банка', early_data['bank_group_price'])
            bank_price = self.read_bank_price(early_data['bank_group_price'])
        # Characteristics may be lazy-loaded near the bottom of the product page.
        for _ in range(3):
            self.page.evaluate('window.scrollBy(0, 1000)')
            self.page.wait_for_timeout(500)
        self.page.evaluate(EXPAND_CHARACTERISTICS)
        self.page.wait_for_timeout(750)
        data = self.page.evaluate(EXTRACT)
        self.last_price_diagnostic['after_scroll'] = self.page.evaluate(PRICE_DIAGNOSTIC)
        self.last_price_diagnostic['parsed_before'] = early_data
        self.last_price_diagnostic['parsed_after'] = dict(data)
        # Scrolling to characteristics can unload the price component. Keep the
        # price already read from the same product before scrolling.
        if not data['price'] and early_data.get('price'):
            data['price'] = early_data['price']
        if data['available'] is None and early_data.get('available') is not None:
            data['available'] = early_data['available']
        fields = data['fields']
        if not data['title']:
            raise RuntimeError('Не распознано название карточки')
        if self.strict_metadata and (not fields.get('Серия') or not (fields.get('Автор') or fields.get('Авторы'))):
            raise RuntimeError('Не распознаны название/автор/серия карточки')
        binding = fields.get('Тип обложки') or fields.get('Переплет') or fields.get('Переплёт', '')
        if self.strict_metadata and not binding:
            raise RuntimeError('Не распознан переплёт карточки')
        sku = re.fullmatch(r'/product/(?:[^/]*-)?(\d+)/?', urlsplit(url).path).group(1)
        offer = self.json_offer(url, sku)
        card_price, available = offer if offer is not None else (
            rubles(data['price']) if data['price'] else bank_price, data['available'])
        book = Book(sku, url, data['title'], fields.get('Автор') or fields.get('Авторы', ''),
                    fields.get('Серия', ''), binding, card_price, available)
        if self.strict_metadata and book.matches and book.available is None:
            raise RuntimeError('Не распознана кнопка покупки/доступность карточки')
        if book.available is True and book.card_price is None:
            save_diagnostic(self.diagnostic_directory, sku, self.last_price_diagnostic)
            raise RuntimeError('Цена с Ozon Картой отсутствует или неоднозначна; сохранена диагностика блока цены')
        return book

    def inspect_price(self, url):
        sku = re.fullmatch(r'/product/(?:[^/]*-)?(\d+)/?', urlsplit(url).path).group(1)
        self.last_price_diagnostic = {}
        try:
            book = self.book(url)
            self.last_price_diagnostic['result'] = {'card_price': str(book.card_price), 'available': book.available}
        except RuntimeError as exc:
            self.last_price_diagnostic['error'] = str(exc)
        return save_diagnostic(self.diagnostic_directory, sku, self.last_price_diagnostic)
