from datetime import datetime
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from ozon_monitor.schedule import next_half_hour, DailyReporter
from ozon_monitor.store import Store


class ClockSchedule(unittest.TestCase):
    def test_half_hour_boundaries_and_midnight(self):
        for value, expected in (
            ('2026-10-25 10:01:05', '2026-10-25 10:30:00'),
            ('2026-10-25 10:30:00', '2026-10-25 11:00:00'),
            ('2026-10-25 11:59:59', '2026-10-25 12:00:00'),
            ('2026-10-25 23:59:59', '2026-10-26 00:00:00'),
        ):
            self.assertEqual(next_half_hour(datetime.fromisoformat(value)), datetime.fromisoformat(expected))


class DailyReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.store.initialize()
        self.telegram = Mock()
        self.current = datetime(2026, 10, 25, 11, 59, 59)
        self.reporter = DailyReporter(self.store, self.telegram, threading.Event(), lambda: self.current)

    def tearDown(self):
        self.tmp.cleanup()

    def test_noon_report_even_when_empty_and_reports_off_and_worker_busy(self):
        self.store.set('reports', 'off')
        self.store.set('worker_state', 'проверка #1')
        self.store.set('last_full_summary', 'Предыдущий обход: ошибок 1')
        self.assertFalse(self.reporter.send_due())
        self.current = datetime(2026, 10, 25, 12, 0)
        self.assertTrue(self.reporter.send_due())
        text = self.telegram.send_text.call_args.args[0]
        self.assertIn('проверка #1', text)
        self.assertIn('ошибок 1', text)
        self.assertFalse(self.reporter.send_due())

    def test_successful_delivery_persists_across_restart_and_new_day(self):
        self.current = datetime(2026, 10, 25, 12, 0)
        self.assertTrue(self.reporter.send_due())
        restarted = DailyReporter(Store(self.path), self.telegram, threading.Event(), lambda: self.current)
        self.assertFalse(restarted.send_due())
        self.current = datetime(2026, 10, 26, 12, 0)
        self.assertTrue(restarted.send_due())

    def test_failed_delivery_retried_and_restart_after_noon_catches_up(self):
        self.current = datetime(2026, 10, 25, 13, 0)
        self.telegram.send_text.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            self.reporter.send_due()
        self.assertEqual(self.store.get('daily_report_date'), '')
        self.telegram.send_text.side_effect = None
        self.assertTrue(self.reporter.send_due())
