import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from ozon_monitor.upstream_source import UpstreamOzon
from ozon_monitor.upstream_transport import UpstreamTransport
from ozon_monitor.exclusions import title_key
from ozon_monitor.rules import Target
from test_monitor import sample
from test_upstream_source import book_payload
from dataclasses import replace
from decimal import Decimal


class GenericTargets(unittest.TestCase):
    def test_single_book_discovery_does_not_scan_initial_king_series(self):
        transport = Mock()
        source = UpstreamOzon(Path('/tmp/monitor'), transport=transport,
                              search_url=sample().url, kind='book', strict_metadata=False)
        self.assertEqual(source.discover(), [sample().url])
        transport.page.assert_not_called()

    def test_single_book_can_have_no_series_and_no_binding_field(self):
        transport = Mock(mode='json')
        data = book_payload()
        del data['widgetStates']['webCharacteristics-1']
        transport.page.return_value = data
        source = UpstreamOzon(Path('/tmp/monitor'), transport=transport,
                              search_url=sample().url, kind='book', strict_metadata=False)
        book = source.book('https://www.ozon.ru/product/book-123/')
        target = Target(1, 'book', book.url, 'Оно', hardcover=False)
        self.assertEqual(target.evaluate(book, Decimal('550')), (True, 'Подходит', False))

    def test_series_urls_are_isolated_from_global_env(self):
        first = 'https://www.ozon.ru/series/new-123/?covertype=537&sorting=price'
        transport = Mock(mode='json')
        from test_upstream_source import search
        transport.page.return_value = search([])
        with patch.dict('os.environ', {'OZON_SEARCH_URL': 'https://www.ozon.ru/series/unrelated-789/'}):
            source = UpstreamOzon(Path('/tmp/monitor'), transport=transport, search_url=first, delay=0)
            source.discover()
        self.assertEqual(transport.page.call_args.args[0], first)

    def test_generic_author_suffix_is_removed_for_exclusions(self):
        self.assertEqual(title_key('Война и мир | Толстой, Лев Николаевич', 'Толстой Лев Николаевич'), 'война и мир')

    def test_explicit_book_probe_checks_product_content_not_search(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as temporary:
            transport = UpstreamTransport(Path(temporary), probe_url=sample().url, probe_kind='book')
            self.assertTrue(transport._search_confirmed(200, json.dumps({'widgetStates': {'webProductHeading-1': '{"title":"Оно"}'}}), 'test'))
            self.assertEqual(transport.probe_url, sample().url)

    def test_missing_required_series_is_unknown_not_wrong_edition(self):
        target = Target(1, 'series', '', 'Новая', series='Новая')
        eligible, _, unknown = target.evaluate(replace(sample(), series=''), Decimal('400'))
        self.assertFalse(eligible)
        self.assertTrue(unknown)

    def test_series_landing_page_without_category_suffix_is_supported(self):
        from ozon_monitor.search_dom import validate_search
        self.assertTrue(validate_search({'path': '/series/new-123/', 'links': ['/product/book-123/']}))
