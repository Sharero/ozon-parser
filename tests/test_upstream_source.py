import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from ozon_monitor.upstream_source import UpstreamOzon, search_page, product_url


def payload(**widgets):
    return {'widgetStates': {key: json.dumps(value) for key, value in widgets.items()}}


def search(ids, next_page=None):
    return payload(**{
        'searchResultsV2-1': {'items': [{'action': {'link': f'/product/book-{sku}/'}} for sku in ids]},
        'megaPaginator-1': {'nextPage': next_page},
    })


def book_payload(price='399,99 ₽', binding='Твердый переплет', series='Король на все времена'):
    return payload(**{
        'webProductHeading-1': {'title': 'Оно'},
        'webCharacteristics-1': {'characteristics': [
            {'name': 'Автор', 'values': [{'text': 'Кинг Стивен'}]},
            {'name': 'Серия', 'values': [{'text': series}]},
            {'name': 'Тип обложки', 'values': [{'text': binding}]},
        ]},
        'webPrice-1': {'link': '/modal?product_id=123', 'cardPrice': price, 'price': '499 ₽',
                       'isAvailable': True, 'lexemes': {'withOzonCard': 'с Ozon Картой'}},
    })


class JsonSeries(unittest.TestCase):
    def test_saved_cookies_with_alternate_json_layout_fall_back_to_confirmed_dom(self):
        source, transport = self.source()
        transport.mode = 'json'
        transport.page.return_value = payload(**{'tileGridDesktop-1': {'items': []}})
        dom = Mock()
        dom.discover.return_value = ['https://www.ozon.ru/product/book-123/']
        with patch.object(source, '_dom_source', return_value=dom):
            self.assertEqual(source.discover(), dom.discover.return_value)
        transport.select_dom.assert_called_once()
        dom.discover.assert_called_once()

    def test_unconfirmed_html_fallback_fails_instead_of_reporting_no_books(self):
        source, transport = self.source()
        transport.page.return_value = payload(**{'header-1': {}})
        transport.select_dom.side_effect = RuntimeError('HTML недоступен')
        with self.assertRaisesRegex(RuntimeError, 'HTML недоступен'):
            source.discover()

    def source(self):
        transport = Mock()
        return UpstreamOzon(Path('/tmp/monitor'), delay=0, transport=transport), transport

    def test_pagination_and_dedup(self):
        source, transport = self.source()
        transport.page.side_effect = [search([123], '/search/?page=2'), search([456]), search([123, 789])]
        self.assertEqual(source.discover(), [f'https://www.ozon.ru/product/book-{sku}/' for sku in (123, 456, 789)])
        self.assertEqual(transport.page.call_count, 3)

    def test_repeated_page_and_limit_fail(self):
        source, transport = self.source()
        transport.page.side_effect = [search([123], 2), search([123], 3)]
        with self.assertRaisesRegex(RuntimeError, 'повторил'):
            source.discover()
        source.max_pages = 1
        transport.page.side_effect = [search([123], 2)]
        with self.assertRaisesRegex(RuntimeError, 'MAX_SEARCH_PAGES'):
            source.discover()

    def test_missing_results_and_unknown_pagination_fail(self):
        with self.assertRaises(RuntimeError):
            search_page({'challengeURL': '/challenge'}, 1)
        result = search([123])
        del result['widgetStates']['megaPaginator-1']
        with self.assertRaisesRegex(RuntimeError, 'пагинацию'):
            search_page(result, 1)

    def test_no_results_is_supported(self):
        self.assertEqual(search_page(search([]), 1), ({}, None))

    def test_complete_book_uses_decimal_card_price(self):
        source, transport = self.source()
        transport.page.return_value = book_payload()
        book = source.book('https://www.ozon.ru/product/book-123/')
        self.assertTrue(book.eligible)
        self.assertEqual(book.card_price, Decimal('399.99'))

    def test_softcover_threshold_and_other_series(self):
        source, transport = self.source()
        for changes in ({'binding': 'Мягкая обложка'}, {'price': '550 ₽'}, {'series': 'Другая серия'}):
            transport.page.return_value = book_payload(**changes)
            self.assertFalse(source.book('https://www.ozon.ru/product/book-123/').eligible)

    def test_unknown_card_price_does_not_use_regular_price(self):
        source, transport = self.source()
        transport.page.return_value = book_payload(price=None)
        with self.assertRaisesRegex(RuntimeError, 'cardPrice'):
            source.book('https://www.ozon.ru/product/book-123/')

    def test_missing_characteristics_are_not_guessed_from_title(self):
        source, transport = self.source()
        result = book_payload()
        del result['widgetStates']['webCharacteristics-1']
        transport.page.return_value = result
        with self.assertRaisesRegex(RuntimeError, 'переплёт'):
            source.book('https://www.ozon.ru/product/book-123/')

    def test_sku_urls_and_foreign_hosts(self):
        self.assertEqual(product_url('/product/123/'), ('123', 'https://www.ozon.ru/product/123/'))
        self.assertIsNone(product_url('https://example.com/product/book-123/'))
