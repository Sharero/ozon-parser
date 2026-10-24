import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit, urlencode
from dataclasses import replace
from decimal import Decimal
from ozon_monitor.bot import BotController, BotPoller
from ozon_monitor.store import Store
from ozon_monitor.rules import DEFAULT_SEARCH_URL
from test_monitor import sample


class OwnerBot(unittest.TestCase):
    def test_command_scan_policy_and_noop_changes(self):
        def check(command, expected):
            self.store.take_checks()
            self.wake.clear()
            self.bot.handle(self.message(command))
            self.assertEqual(self.store.take_checks(), expected, command)
            self.assertEqual(self.wake.is_set(), bool(expected), command)
        check('/price 1 400', [1])
        check('/price 1 400', [])
        check('/price 400', [])
        check('/defaultprice 900', [])
        check('/exclude Оно', [])
        check('/exclude Оно', [])
        check('/include Оно', [0])
        check('/include Оно', [])
        check('/pause 1', [])
        check('/price 1 450', [])
        check('/check 1', [])
        check('/resume 1', [1])
        check('/resume 1', [])
        check('/check 1', [1])
        check('/check', [0])
        for command in ('/list', '/status', '/reports off', '/reports on', '/help', '/exclusions', '/cancel'):
            check(command, [])

    def test_individual_prices_on_add_and_duplicate_do_not_reset_price(self):
        url = 'https://www.ozon.ru/series/new-12/'
        self.bot.handle(self.message('/addseries ' + url + ' | Новая | | 800'))
        target = self.store.targets()[-1]
        self.assertEqual(target.price_limit, 800)
        self.assertEqual(self.store.take_checks(), [target.id])
        self.bot.handle(self.message('/addseries ' + url + ' | Новая'))
        self.assertEqual(self.store.take_checks(), [])
        self.assertEqual(self.store.snapshot(target.id)[1], 800)
        self.bot.handle(self.message('/addseries ' + url + ' | Новая | Автор'))
        self.assertEqual(self.store.take_checks(), [target.id])
        self.bot.handle(self.message('/addbook ' + sample().url + ' | Оно | 300'))
        book = self.store.targets()[-1]
        self.assertEqual(book.price_limit, 300)
        self.assertEqual(self.store.take_checks(), [book.id])
        self.assertEqual(self.store.snapshot(1)[1], 550)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.store.initialize()
        self.telegram = Mock()
        self.wake = threading.Event()
        self.bot = BotController(self.store, self.telegram, 12345, self.wake)

    def tearDown(self):
        self.tmp.cleanup()

    def message(self, text, sender=12345, chat=12345):
        return {'update_id': 7, 'message': {'from': {'id': sender}, 'chat': {'id': chat}, 'text': text}}

    def test_strangers_and_groups_cannot_change_settings_or_get_replies(self):
        self.bot.handle(self.message('/price 1', sender=999))
        self.bot.handle(self.message('/price 1', chat=-100))
        self.assertEqual(self.store.get('price_limit'), '550')
        self.telegram.send_text.assert_not_called()

    def test_price_command_changes_persistent_limit_and_requests_check(self):
        self.bot.handle(self.message('/price 1 400'))
        self.assertEqual(self.store.snapshot(1)[1], 400)
        self.assertTrue(self.wake.is_set())
        self.assertEqual(self.telegram.send_text.call_args.kwargs['chat_id'], 12345)

    def test_pending_input_survives_controller_restart_and_can_be_cancelled(self):
        self.bot.handle(self.message('💰 Порог'))
        restarted = BotController(self.store, self.telegram, 12345, self.wake)
        restarted.handle(self.message('1 399,90'))
        self.assertEqual(str(self.store.snapshot(1)[1]), '399.90')
        self.bot.handle(self.message('/exclude'))
        self.bot.handle(self.message('/cancel'))
        self.bot.handle(self.message('Оно'))
        self.assertEqual(self.store.exclusions(), {})

    def test_invalid_prices_do_not_change_previous_limit(self):
        for value in ('-1', '0', 'NaN', 'Infinity', 'one', '400.001'):
            self.bot.handle(self.message('/price 1 ' + value))
        self.assertEqual(self.store.get('price_limit'), '550')

    def test_exclude_and_include(self):
        self.bot.handle(self.message('/exclude Зелёная миля'))
        self.assertIn('зеленая миля', self.store.exclusions())
        self.bot.handle(self.message('/include Зеленая миля'))
        self.assertEqual(self.store.exclusions(), {})

    def test_add_generic_series_and_book_and_pause(self):
        self.bot.handle(self.message('/addseries https://www.ozon.ru/series/example-123/ | Новая серия | Другой автор'))
        self.bot.handle(self.message('/addbook ' + sample().url + ' | Моя книга'))
        series, book = self.store.targets()[1:]
        self.assertEqual(series.series, 'Новая серия')
        self.assertEqual(series.author, 'Другой автор')
        self.assertIn('sorting=price', series.url)
        self.assertTrue(series.hardcover)
        self.assertFalse(book.hardcover)
        self.bot.handle(self.message('/pause ' + str(book.id)))
        self.assertNotIn(book.id, [t.id for t in self.store.targets(True)])
        self.bot.handle(self.message('/resume ' + str(book.id)))
        self.assertIn(book.id, [t.id for t in self.store.targets(True)])

    def test_foreign_hosts_or_product_url_as_series_rejected(self):
        self.bot.handle(self.message('/addbook https://example.com/product/book-1/'))
        self.bot.handle(self.message('/addseries ' + sample().url + ' | Серия'))
        self.assertEqual(len(self.store.targets()), 1)

    def test_category_search_preserves_query_and_filters_exact_series(self):
        url = 'https://www.ozon.ru/category/knigi-16500/?' + urlencode({
            'text': 'лавкрафт хроники некрономикона', 'from_global': 'true',
            'covertype': '538', 'sorting': 'popular', 'page': '3'})
        self.bot.handle(self.message('/addseries ' + url + ' | Хроники Некрономикона'))
        target = self.store.targets()[-1]
        self.assertEqual(target.series, 'Хроники Некрономикона')
        query = parse_qs(urlsplit(target.url).query)
        self.assertEqual(query['text'], ['лавкрафт хроники некрономикона'])
        self.assertEqual(query['covertype'], ['537'])
        self.assertEqual(query['sorting'], ['price'])
        self.assertNotIn('page', query)
        other = replace(sample('399'), author='Лавкрафт', series='Другая серия')
        self.assertFalse(target.evaluate(other, Decimal('550'))[0])
        matching = replace(other, series='Хроники Некрономикона')
        self.assertTrue(target.evaluate(matching, Decimal('550'))[0])

    def test_search_series_requires_query_and_exact_series_name(self):
        before = len(self.store.targets())
        for url in ('https://www.ozon.ru/category/knigi-16500/',
                    'https://www.ozon.ru/search/?text=%20',
                    'https://example.com/search/?text=books'):
            self.bot.handle(self.message('/addseries ' + url + ' | Серия'))
        self.bot.handle(self.message('/addseries https://www.ozon.ru/search/?text=books'))
        self.assertEqual(len(self.store.targets()), before)

    def test_search_page_is_supported_as_series_source(self):
        self.bot.handle(self.message('/addseries https://www.ozon.ru/search/?text=books | Серия'))
        self.assertEqual(self.store.targets()[-1].series, 'Серия')

    def test_processed_offset_survives_poller_restart(self):
        stop = threading.Event()
        def updates(offset):
            self.assertEqual(offset, 0)
            return [self.message('/exclude Оно')]
        self.telegram.updates.side_effect = updates
        original_set = self.store.set
        def set_value(key, value):
            original_set(key, value)
            if key == 'update_offset':
                stop.set()
        self.store.set = set_value
        BotPoller(self.bot, stop).run()
        self.assertEqual(self.store.get('update_offset'), '8')
        self.assertIn('оно', self.store.exclusions())
