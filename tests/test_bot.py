import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from ozon_monitor.bot import BotController, BotPoller
from ozon_monitor.store import Store
from ozon_monitor.rules import DEFAULT_SEARCH_URL
from test_monitor import sample


class OwnerBot(unittest.TestCase):
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
        self.bot.handle(self.message('/price 400'))
        self.assertEqual(self.store.get('price_limit'), '400')
        self.assertTrue(self.wake.is_set())
        self.assertEqual(self.telegram.send_text.call_args.kwargs['chat_id'], 12345)

    def test_pending_input_survives_controller_restart_and_can_be_cancelled(self):
        self.bot.handle(self.message('💰 Порог'))
        restarted = BotController(self.store, self.telegram, 12345, self.wake)
        restarted.handle(self.message('399,90'))
        self.assertEqual(self.store.get('price_limit'), '399.90')
        self.bot.handle(self.message('/exclude'))
        self.bot.handle(self.message('/cancel'))
        self.bot.handle(self.message('Оно'))
        self.assertEqual(self.store.exclusions(), {})

    def test_invalid_prices_do_not_change_previous_limit(self):
        for value in ('-1', '0', 'NaN', 'Infinity', 'one', '400.001'):
            self.bot.handle(self.message('/price ' + value))
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
