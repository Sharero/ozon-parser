import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from ozon_monitor.search_config import search_urls, page_url
from ozon_monitor.search_dom import validate_search

SERIES = 'https://www.ozon.ru/series/korol-na-vse-vremena-21102932/category/knigi-16500/?covertype=537'


class FilteredSearch(unittest.TestCase):
    @patch.dict('os.environ', {'OZON_SEARCH_URL': SERIES})
    def test_configured_series_replaces_broad_queries(self):
        self.assertEqual(search_urls(['query one', 'query two']), [SERIES])

    def test_pagination_preserves_binding_filter_and_series(self):
        for number in (1, 2, 3):
            url = page_url(SERIES, number)
            self.assertEqual(urlsplit(url).path, urlsplit(SERIES).path)
            parameters = parse_qs(urlsplit(url).query)
            self.assertEqual(parameters['covertype'], ['537'])
            self.assertEqual(parameters.get('page', ['1']), [str(number)])

    def test_previous_page_is_replaced_without_duplicates(self):
        url = page_url(SERIES + '&page=5', 2)
        self.assertEqual(parse_qs(urlsplit(url).query)['page'], ['2'])

    def test_series_grid_does_not_need_search_text(self):
        self.assertTrue(validate_search({'path': urlsplit(SERIES).path, 'links': ['/product/book-123/']}))

    @patch.dict('os.environ', {'OZON_SEARCH_URL': 'https://example.com/series/'})
    def test_foreign_search_url_rejected(self):
        with self.assertRaises(ValueError):
            search_urls(['query'])

    def test_sorted_series_keeps_filter_and_price_sort_on_every_page(self):
        base = SERIES + '&sorting=price'
        for number in (1, 2, 10):
            query = parse_qs(urlsplit(page_url(base, number)).query)
            self.assertEqual(query['covertype'], ['537'])
            self.assertEqual(query['sorting'], ['price'])
