from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from ozon_monitor.store import Store
from ozon_monitor.state import State
from test_monitor import sample


class PersistentSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_exclusions_and_targets_survive_restart(self):
        self.store.set_price('400')
        self.store.exclude('Зелёная миля')
        target = self.store.add_target('book', sample().url, 'Оно')
        restarted = Store(self.path)
        restarted.initialize()
        self.assertEqual(restarted.get('price_limit'), '400')
        self.assertIn('зеленая миля', restarted.exclusions())
        self.assertIn(target.id, [t.id for t in restarted.targets()])
        self.assertEqual(len(restarted.targets()), 2)

    def test_revision_does_not_consume_notification_before_successful_delivery(self):
        book = sample()
        self.store.record(1, book, True, 0, notified=True)
        self.assertFalse(self.store.needs_notification(1, book, True, 0))
        self.store.set_price('400')
        _, _, revision, _ = self.store.snapshot(1)
        self.assertTrue(self.store.needs_notification(1, book, True, revision))
        self.store.record(1, book, True, revision, notified=False)
        self.assertTrue(self.store.needs_notification(1, book, True, revision))
        self.store.record(1, book, True, revision, notified=True)
        self.assertFalse(self.store.needs_notification(1, book, True, revision))

    def test_same_sku_has_separate_history_for_each_target(self):
        book = sample()
        other = self.store.add_target('book', book.url)
        self.store.record(1, book, True, 0, notified=True)
        self.assertTrue(self.store.needs_notification(other.id, book, True, 0))

    def test_parallel_command_writes_and_monitor_reads(self):
        def write(number):
            self.store.exclude(f'Книга {number}')
            return self.store.snapshot(1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(write, range(20)))
        self.assertEqual(len(self.store.exclusions()), 20)
        self.assertTrue(all(snapshot[1] == Decimal('550') for snapshot in results))

    def test_add_duplicate_is_idempotent_and_disabled_target_resumes(self):
        first = self.store.add_target('book', sample().url)
        self.store.enable(first.id, False)
        second = self.store.add_target('book', sample().url)
        self.assertEqual(first.id, second.id)
        self.assertTrue(second.enabled)

    def test_initialization_imports_old_history_and_exclusions_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'legacy.sqlite3'
            legacy = State(path)
            legacy.record(sample(), notified=True)
            legacy.close()
            exclusions = Path(temporary) / 'excluded.txt'
            exclusions.write_text('Сияние\n')
            store = Store(path)
            store.initialize(exclusions_file=exclusions)
            self.assertFalse(store.needs_notification(1, sample(), True, 0))
            store.include('Сияние')
            store.initialize(exclusions_file=exclusions)
            self.assertNotIn('сияние', store.exclusions())
