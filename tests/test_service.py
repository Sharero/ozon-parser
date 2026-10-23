import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from ozon_monitor.store import Store
from ozon_monitor.service import Monitor
from ozon_monitor.diagnostics import AgeConfirmationRequired
from test_monitor import sample


class RuntimeMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.store = Store(self.directory / 'state.sqlite3')
        self.store.initialize()
        self.telegram, self.factory = Mock(), Mock()
        self.monitor = Monitor(self.store, self.telegram, self.directory, threading.Event(), factory=self.factory, delay=0)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, books):
        source = Mock()
        source.discover.return_value = [f'url-{i}' for i in range(len(books))]
        source.book.side_effect = books
        return source

    def test_age_gate_alerts_immediately_with_reports_off_and_closes_source(self):
        self.store.set('reports', 'off')
        source = self.source([AgeConfirmationRequired('Требуется возраст'), sample('399')])
        self.factory.return_value = source
        self.assertFalse(self.monitor.run_all())
        source.book.assert_called_once()
        source.close.assert_called_once()
        self.telegram.send.assert_not_called()
        self.telegram.send_text.assert_called_once()
        self.assertIn('url-0', self.telegram.send_text.call_args.args[0])

    def test_age_gate_during_discovery_also_alerts(self):
        self.store.set('reports', 'off')
        source = self.source([])
        source.discover.side_effect = AgeConfirmationRequired('Требуется возраст')
        self.factory.return_value = source
        self.assertFalse(self.monitor.run_all())
        self.telegram.send_text.assert_called_once()
        source.close.assert_called_once()

    def test_cutoff_stops_only_current_series_then_checks_single_book(self):
        direct = self.store.add_target('book', sample().url)
        first = self.source([sample('551'), sample('399')])
        second = self.source([replace(sample('399'), author='Другой автор', series='', binding='Мягкая обложка')])
        self.factory.side_effect = [first, second]
        self.assertTrue(self.monitor.run_all())
        first.book.assert_called_once()
        second.book.assert_called_once()
        self.telegram.send.assert_called_once()
        self.assertIn('остановка выше порога', self.store.get('last_summary'))
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_new_price_and_exclusions_apply_during_active_cycle(self):
        source = self.source([sample('450')])
        def book(_):
            self.store.set_price('400')
            return sample('450')
        source.book.side_effect = book
        self.factory.return_value = source
        self.assertTrue(self.monitor.run_all())
        self.telegram.send.assert_not_called()
        self.assertIn('Порог: строго ниже 400', self.store.get('last_summary'))

    def test_new_exclusion_is_read_after_card_finishes_loading(self):
        source = self.source([sample('399')])
        def book(_):
            self.store.exclude('Оно')
            return sample('399')
        source.book.side_effect = book
        self.factory.return_value = source
        self.assertTrue(self.monitor.run_all())
        self.telegram.send.assert_not_called()
        self.assertIn('исключено 1', self.store.get('last_summary'))

    def test_generic_series_no_longer_requires_stephen_king(self):
        target = self.store.add_target('series', 'https://www.ozon.ru/series/new-123/', 'Новая', 'Новая', 'Другой Автор')
        book = replace(sample('399'), author='Автор Другой', series='Новая')
        self.factory.return_value = self.source([book])
        result = self.monitor.target_cycle(target)
        self.assertEqual(result.notified, 1)
        self.telegram.send.assert_called_once_with(book)

    def test_failed_delivery_does_not_consume_notification(self):
        self.factory.side_effect = [self.source([sample('399')]), self.source([sample('399')])]
        self.telegram.send.side_effect = [RuntimeError('delivery failed'), None]
        self.assertFalse(self.monitor.run_all())
        self.assertTrue(self.monitor.run_all())
        self.assertEqual(self.telegram.send.call_count, 2)

    def test_source_failure_reported_and_browser_closed(self):
        source = Mock()
        source.discover.side_effect = RuntimeError('Ozon HTTP 403')
        self.factory.return_value = source
        self.assertFalse(self.monitor.run_all())
        self.assertIn('Ozon HTTP 403', self.store.get('last_summary'))
        self.telegram.send_text.assert_called_once()
        source.close.assert_called_once()

    def test_pausing_target_during_card_load_prevents_notification(self):
        source = self.source([sample('399')])
        def book(_):
            self.store.enable(1, False)
            return sample('399')
        source.book.side_effect = book
        self.factory.return_value = source
        self.monitor.run_all()
        self.telegram.send.assert_not_called()

    def test_unknown_metadata_does_not_reset_delivered_history(self):
        self.store.record(1, sample(), True, 0, notified=True)
        self.factory.return_value = self.source([replace(sample(), series='')])
        self.assertFalse(self.monitor.run_all())
        self.assertFalse(self.store.needs_notification(1, sample(), True, 0))
