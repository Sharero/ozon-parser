import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from ozon_monitor.models import Book, rubles
from ozon_monitor.state import State


def sample(price='399'):
    return Book('123', 'https://www.ozon.ru/product/book-123/', 'Оно', 'Кинг Стивен',
                'Король на все времена', 'Твёрдый переплёт', Decimal(price), True)


class Filters(unittest.TestCase):
    def test_strict_threshold_and_availability(self):
        for price, expected in [('400', True), ('549.99', True), ('550', False), ('550.01', False), ('0', False)]:
            self.assertEqual(sample(price).eligible, expected)
        self.assertFalse(replace(sample(), available=False).eligible)
        self.assertFalse(replace(sample(), available=None).eligible)
        self.assertFalse(replace(sample(), card_price=None).eligible)

    def test_exact_edition(self):
        for changes in ({'author': 'Джо Хилл'}, {'author': 'Стивен Кингсли'},
                        {'series': 'Король на все времена (другая серия)'},
                        {'binding': 'Мягкая обложка'}, {'binding': ''}):
            self.assertFalse(replace(sample(), **changes).eligible)

    def test_price_parsing(self):
        self.assertEqual(rubles('1\u202f399,90 ₽'), Decimal('1399.90'))
        self.assertIsNone(rubles('399 ₽ 499 ₽'))
        self.assertIsNone(rubles('399 баллов'))


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.db'
        self.state = State(self.path)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_restart_drop_and_return(self):
        book = sample()
        self.assertTrue(self.state.needs_notification(book))
        self.state.record(book, notified=True)
        self.state.close()
        self.state = State(self.path)
        self.assertFalse(self.state.needs_notification(book))
        self.assertTrue(self.state.needs_notification(sample('350')))
        self.state.record(sample('350'), notified=True)
        self.assertFalse(self.state.needs_notification(sample('370')))
        self.state.record(sample('551'))
        self.assertTrue(self.state.needs_notification(book))

    def test_failed_send_is_retried_next_cycle(self):
        from main import run_cycle
        source, telegram = Mock(), Mock()
        source.discover.return_value = [sample().url]
        source.book.return_value = sample()
        telegram.send.side_effect = RuntimeError('delivery failed')
        self.assertFalse(run_cycle(source, self.state, telegram))
        self.assertTrue(self.state.needs_notification(sample()))
        telegram.send.side_effect = None
        self.assertTrue(run_cycle(source, self.state, telegram))
        self.assertFalse(self.state.needs_notification(sample()))

    def test_ineligible_resets_availability(self):
        self.state.record(sample(), notified=True)
        self.state.record(replace(sample(), available=False))
        self.assertTrue(self.state.needs_notification(sample()))

    def test_skip_reason_is_logged_with_price(self):
        from main import run_cycle
        source, telegram = Mock(), Mock()
        source.discover.return_value = [sample().url]
        source.book.return_value = sample('600')
        with self.assertLogs('monitor', level='INFO') as logs:
            self.assertTrue(run_cycle(source, self.state, telegram))
        output = '\n'.join(logs.output)
        self.assertIn('цена с Ozon Картой=600', output)
        self.assertIn('цена вне порога <550', output)
        telegram.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
