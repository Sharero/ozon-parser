import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from ozon_monitor.selenium_dom import SeleniumPage, SeleniumDOMOzon, DOMModeSelected
from ozon_monitor.upstream_source import UpstreamOzon


class SeleniumAdapter(unittest.TestCase):
    def test_locator_filter_and_array_evaluation(self):
        driver = Mock()
        next_link, other = Mock(), Mock()
        next_link.text, other.text = 'Дальше', 'Главная'
        driver.find_elements.return_value = [next_link, other]
        import re
        locator = SeleniumPage(driver).locator('a').filter(has_text=re.compile('^Дальше$'))
        self.assertEqual(locator.count(), 1)
        locator.evaluate_all('(nodes) => nodes.map(n => n.href)')
        self.assertEqual(driver.execute_script.call_args.args[1], [next_link])

    def test_visible_price_parser_has_no_api_call(self):
        source = SeleniumDOMOzon(Mock(), delay=0)
        source.page = Mock()
        source.goto = Mock()
        source.page.evaluate.return_value = {
            'title': 'Оно', 'fields': {'Автор': 'Кинг Стивен', 'Серия': 'Король на все времена',
                                      'Тип обложки': 'Твердый переплет'},
            'price': '399 ₽', 'available': True,
        }
        self.assertTrue(source.book('https://www.ozon.ru/product/123/').eligible)
        self.assertFalse(any('/api/' in call.args[0] for call in source.page.evaluate.call_args_list))

    def test_unknown_card_price_does_not_send(self):
        source = SeleniumDOMOzon(Mock(), delay=0)
        source.page = Mock()
        source.goto = Mock()
        source.page.evaluate.return_value = {
            'title': 'Оно', 'fields': {'Автор': 'Кинг Стивен', 'Серия': 'Король на все времена',
                                      'Тип обложки': 'Твердый переплет'},
            'price': None, 'available': True,
        }
        with self.assertRaisesRegex(RuntimeError, 'Цена с Ozon Картой'):
            source.book('https://www.ozon.ru/product/book-123/')

    def test_price_is_kept_when_scrolling_unloads_price_widget(self):
        source = SeleniumDOMOzon(Mock(), delay=0)
        source.goto = Mock()
        source.page = Mock()
        before = {'title': 'Оно', 'fields': {}, 'price': '499 ₽', 'available': True}
        after = {'title': 'Оно', 'fields': {'Автор': 'Кинг Стивен', 'Серия': 'Король на все времена',
                                          'Тип обложки': 'Твердый переплет'},
                 'price': None, 'available': None}
        source.page.evaluate.side_effect = [before, {}, None, None, None, None, after, {}]
        book = source.book('https://www.ozon.ru/product/book-123/')
        self.assertTrue(book.eligible)
        self.assertIsNone(source.last_price_diagnostic['parsed_after']['price'])

    @patch('ozon_monitor.upstream_source.SeleniumDOMOzon')
    def test_source_routes_existing_dom_mode_without_json(self, dom):
        transport = Mock(mode='dom')
        source = UpstreamOzon(Path('/tmp/monitor'), transport=transport)
        dom.return_value.discover.return_value = ['book']
        self.assertEqual(source.discover(), ['book'])
        source.book('book')
        transport.page.assert_not_called()
        dom.return_value.book.assert_called_once_with('book')

    @patch('ozon_monitor.upstream_source.SeleniumDOMOzon')
    def test_json_to_dom_switch_restarts_discovery(self, dom):
        transport = Mock(mode='json')
        transport.page.side_effect = DOMModeSelected()
        source = UpstreamOzon(Path('/tmp/monitor'), transport=transport, delay=0)
        dom.return_value.discover.return_value = ['book']
        self.assertEqual(source.discover(), ['book'])
        dom.return_value.discover.assert_called_once()


class SearchRecognition(unittest.TestCase):
    def test_search_links_do_not_require_legacy_widget(self):
        from ozon_monitor.search_dom import validate_search
        self.assertTrue(validate_search({'path': '/search/', 'links': ['/product/book-123/'],
                                         'widgets': ['tileGridDesktop']}))

    def test_manual_navigation_is_reported(self):
        from ozon_monitor.search_dom import validate_search
        with self.assertRaisesRegex(RuntimeError, 'Вкладка ушла'):
            validate_search({'path': '/product/book-123/', 'links': ['/product/book-123/']})

    def test_challenge_is_not_success_despite_product_links(self):
        from ozon_monitor.search_dom import validate_search
        with self.assertRaisesRegex(RuntimeError, 'Цены недоступны'):
            validate_search({'path': '/search/', 'links': ['/product/book-123/'],
                             'challenge': 'Похоже, нет соединения fab_chlg_example'})

    def test_timeout_describes_actual_layout(self):
        source = SeleniumDOMOzon(Mock(), delay=0)
        source.page = Mock()
        source.page.evaluate.return_value = {'path': '/search/', 'title': 'Книги',
                                            'links': [], 'widgets': ['newLayout']}
        with self.assertRaisesRegex(RuntimeError, 'виджеты=newLayout'):
            source.wait_search(timeout=0.5)

    def test_confirm_search_uses_shared_recognition(self):
        source = SeleniumDOMOzon(Mock(), delay=0)
        source.goto = Mock()
        source.wait_search = Mock(return_value={'path': '/search/', 'title': 'Книги',
                                                'links': ['/product/book-123/'],
                                                'widgets': ['tileGridDesktop']})
        self.assertTrue(source.confirm_search('https://www.ozon.ru/search/?text=Кинг'))

    def test_category_redirect_with_search_query_is_valid(self):
        from ozon_monitor.search_dom import validate_search
        self.assertTrue(validate_search({'path': '/category/knigi-16500/', 'query': 'Стивен Кинг',
                                         'links': ['/product/book-123/']}))
        with self.assertRaisesRegex(RuntimeError, 'Вкладка ушла'):
            validate_search({'path': '/category/knigi-16500/', 'links': ['/product/book-123/']})
