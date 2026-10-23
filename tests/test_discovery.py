import unittest
from unittest.mock import Mock
from ozon_monitor.ozon import Ozon


class Discovery(unittest.TestCase):
    def source(self, results, pages):
        page = Mock()
        source = Ozon(page, delay=0)
        source.goto = Mock()
        source.wait_search = Mock()
        source.search_snapshot = Mock(side_effect=[{
            'path': '/search/', 'links': links, 'empty': not links,
            'next_links': pages[index] if index < len(pages) else [], 'next_button': False,
        } for index, links in enumerate(results)])
        return source

    def test_pagination_and_deduplication_across_queries(self):
        source = self.source([
            ['/product/ono-1/?x=1'], ['/product/ono-2/', '/product/ono-1/'],
            ['/product/ono-2/', '/product/ono-3/'],
        ], [['https://www.ozon.ru/search/?page=2'], [], []])
        self.assertEqual(source.discover(), [
            'https://www.ozon.ru/product/ono-1/',
            'https://www.ozon.ru/product/ono-2/',
            'https://www.ozon.ru/product/ono-3/',
        ])
        self.assertEqual(source.goto.call_count, 4)

    def test_repeated_page_fails(self):
        source = self.source([['/product/ono-1/'], ['/product/ono-1/']],
                             [['https://www.ozon.ru/search/?page=2']])
        with self.assertRaisesRegex(RuntimeError, 'повторил'):
            source.discover()

    def test_limit_is_not_reported_as_complete(self):
        source = self.source([['/product/ono-1/']], [['https://www.ozon.ru/search/?page=2']])
        source.max_pages = 1
        with self.assertRaisesRegex(RuntimeError, 'MAX_SEARCH_PAGES'):
            source.discover()

    def test_no_results(self):
        source = self.source([[], []], [])
        self.assertEqual(source.discover(), [])
