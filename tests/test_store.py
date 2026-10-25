from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from ozon_monitor.store import Store
from ozon_monitor.state import State
from test_monitor import sample


class PersistentSettings(unittest.TestCase):
    def test_legacy_global_price_is_copied_to_every_target_once_with_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'old.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript('''
                    CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    INSERT INTO settings VALUES ('price_limit','425'),('price_revision','7'),('initialized','1');
                    CREATE TABLE targets(id INTEGER PRIMARY KEY,kind TEXT,url TEXT UNIQUE,name TEXT,
                        series TEXT DEFAULT '',author TEXT DEFAULT '',hardcover INTEGER DEFAULT 1,enabled INTEGER DEFAULT 1);
                    INSERT INTO targets VALUES(1,'book','https://www.ozon.ru/product/book-123/','Оно','','',0,1);
                    INSERT INTO targets VALUES(2,'series','https://www.ozon.ru/series/test-12/','Серия','Серия','',1,1);
                    CREATE TABLE target_offers(target_id INTEGER,sku TEXT,eligible INTEGER,notified_price TEXT,
                        price_revision INTEGER DEFAULT 0,checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(target_id,sku));
                    INSERT INTO target_offers(target_id,sku,eligible,notified_price,price_revision) VALUES(1,'123',1,'399',7);
                ''')
            migrated = Store(path)
            for target in migrated.targets():
                self.assertEqual(target.price_limit, Decimal('425'))
                self.assertEqual(target.price_revision, 7)
            self.assertFalse(migrated.needs_notification(1, sample(), True, 7))
            migrated.set_price('300', 1)
            migrated.set_price('600')
            restarted = Store(path)
            self.assertEqual(restarted.snapshot(1)[1:3], (Decimal('300'), 8))
            self.assertEqual(restarted.snapshot(2)[1:3], (Decimal('425'), 7))

    def test_default_price_only_applies_to_future_targets(self):
        self.store.set_price('800')
        target = self.store.add_target('book', sample().url)
        self.assertEqual(target.price_limit, 800)
        self.assertEqual(self.store.snapshot(1)[1], 550)
        self.store.set_price('300')
        duplicate = self.store.add_target('book', sample().url)
        self.assertEqual(duplicate.price_limit, 800)

    def test_pause_cancels_queued_target_check(self):
        self.store.request_check(1, immediate=True)
        self.store.enable(1, False)
        self.assertEqual(self.store.get('check_urgent'), '')
        self.assertEqual(self.store.take_checks(), [])

    def test_target_price_changes_do_not_touch_other_target_history(self):
        second = self.store.add_target('book', sample().url, limit='700')
        self.store.record(second.id, sample(), True, 0, notified=True)
        self.store.set_price('350', 1)
        self.assertEqual(self.store.snapshot(1)[1:3], (Decimal('350'), 1))
        self.assertEqual(self.store.snapshot(second.id)[1:3], (Decimal('700'), 0))
        self.assertFalse(self.store.needs_notification(second.id, sample(), True, 0))
        self.store.set_price('350', 1)
        self.assertEqual(self.store.snapshot(1)[2], 1)

    def test_check_requests_are_coalesced_and_persisted(self):
        self.store.request_check(1)
        self.store.request_check(1)
        self.assertEqual(Store(self.path).take_checks(), [1])
        self.assertFalse(self.store.has_checks())
        self.store.request_check(1)
        self.store.request_check()
        self.store.request_check(2)
        self.assertEqual(self.store.take_checks(), [0])
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
        self.store.set_price('400', 1)
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
