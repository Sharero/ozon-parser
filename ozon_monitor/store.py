"""SQLite settings and per-target notification history. Each operation owns its connection."""
from contextlib import contextmanager
from decimal import Decimal
import sqlite3
from pathlib import Path
from .exclusions import title_key
from .rules import Target, DEFAULT_SEARCH_URL, price_limit, watch_url


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, series TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
                    hardcover INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS excluded_titles (key TEXT PRIMARY KEY, title TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS check_requests (target_id INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS target_offers (
                    target_id INTEGER NOT NULL, sku TEXT NOT NULL, eligible INTEGER NOT NULL,
                    notified_price TEXT, price_revision INTEGER NOT NULL DEFAULT 0,
                    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(target_id, sku));
            ''')
            for key, value in {'price_limit': '550', 'price_revision': '0', 'reports': 'on', 'update_offset': '0'}.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', (key, value))
            columns = {row['name'] for row in db.execute('PRAGMA table_info(targets)')}
            if 'price_limit' not in columns:
                db.execute("ALTER TABLE targets ADD COLUMN price_limit TEXT NOT NULL DEFAULT '550'")
                db.execute("UPDATE targets SET price_limit=(SELECT value FROM settings WHERE key='price_limit')")
            if 'price_revision' not in columns:
                db.execute('ALTER TABLE targets ADD COLUMN price_revision INTEGER NOT NULL DEFAULT 0')
                db.execute("UPDATE targets SET price_revision=CAST((SELECT value FROM settings WHERE key='price_revision') AS INTEGER)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=''):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    def initialize(self, initial_url=DEFAULT_SEARCH_URL, exclusions_file=None):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
                return
            db.execute('''INSERT OR IGNORE INTO targets(kind,url,name,series,author,hardcover,price_limit)
                VALUES ('series', ?, 'Король на все времена', 'Король на все времена', 'Стивен Кинг', 1,
                (SELECT value FROM settings WHERE key='price_limit'))''', (watch_url(initial_url, 'series'),))
            if exclusions_file is not None and exclusions_file.exists():
                for line in exclusions_file.read_text(encoding='utf-8-sig').splitlines():
                    title = line.strip()
                    if title and not title.startswith('#') and title_key(title):
                        db.execute('INSERT OR IGNORE INTO excluded_titles VALUES (?, ?)', (title_key(title), title))
            # Preserve the old monitor's alert history for the initial target.
            target = db.execute('SELECT id FROM targets WHERE url=?', (watch_url(initial_url, 'series'),)).fetchone()
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='offers'").fetchone()
            if legacy and target:
                db.execute('''INSERT OR IGNORE INTO target_offers(target_id,sku,eligible,notified_price,checked_at)
                    SELECT ?,sku,eligible,notified_price,checked_at FROM offers''', (target[0],))
            db.execute("INSERT INTO settings VALUES ('initialized', '1')")

    def set_price(self, value, target_id=None):
        amount = price_limit(value)
        with self.connect() as db:
            if target_id is not None:
                row = db.execute('SELECT price_limit FROM targets WHERE id=?', (target_id,)).fetchone()
                if row is None:
                    raise ValueError('Такого ID мониторинга нет')
                if Decimal(row[0]) != amount:
                    db.execute('UPDATE targets SET price_limit=?,price_revision=price_revision+1 WHERE id=?', (str(amount), target_id))
                return amount
            old = Decimal(db.execute("SELECT value FROM settings WHERE key='price_limit'").fetchone()[0])
            if old != amount:
                db.execute("UPDATE settings SET value=? WHERE key='price_limit'", (str(amount),))
                db.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='price_revision'")
        return amount

    @staticmethod
    def target(row):
        return Target(row['id'], row['kind'], row['url'], row['name'], row['series'], row['author'], bool(row['hardcover']), bool(row['enabled']), Decimal(row['price_limit']), row['price_revision']) if row else None

    def targets(self, enabled_only=False):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM targets' + (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY id').fetchall()
        return [self.target(row) for row in rows]

    def add_target(self, kind, url, name='', series='', author='', limit=None):
        url = watch_url(url, kind)
        if kind == 'series' and not series.strip():
            raise ValueError('Укажите точное название серии после |, например URL | Король на все времена')
        if any(len(value) > 250 for value in (name, series, author)):
            raise ValueError('Название/автор должны быть короче 250 символов')
        name = name.strip() or (series.strip() if kind == 'series' else 'Книга ' + url.rstrip('/').rsplit('-', 1)[-1].rsplit('/', 1)[-1])
        with self.connect() as db:
            previous = db.execute('SELECT * FROM targets WHERE url=?', (url,)).fetchone()
            amount = price_limit(limit) if limit is not None else (
                Decimal(previous['price_limit']) if previous else Decimal(db.execute("SELECT value FROM settings WHERE key='price_limit'").fetchone()[0]))
            revision = previous['price_revision'] if previous else 0
            if previous and (Decimal(previous['price_limit']) != amount or previous['series'] != series.strip()
                             or previous['author'] != author.strip()):
                revision += 1
            db.execute('''INSERT INTO targets(kind,url,name,series,author,hardcover,price_limit,price_revision) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET enabled=1,name=excluded.name,series=excluded.series,author=excluded.author,
                hardcover=excluded.hardcover,price_limit=excluded.price_limit,price_revision=excluded.price_revision''',
                (kind, url, name, series.strip(), author.strip(), int(kind == 'series'), str(amount), revision))
            row = db.execute('SELECT * FROM targets WHERE url=?', (url,)).fetchone()
        return self.target(row)

    def enable(self, target_id, enabled):
        with self.connect() as db:
            row = db.execute('SELECT enabled FROM targets WHERE id=?', (target_id,)).fetchone()
            if row is None:
                raise ValueError('Такого ID мониторинга нет')
            db.execute('UPDATE targets SET enabled=? WHERE id=?', (int(enabled), target_id))
            if not enabled:
                db.execute('DELETE FROM check_requests WHERE target_id=?', (target_id,))
        return bool(row[0]) != bool(enabled)

    def request_check(self, target_id=None):
        with self.connect() as db:
            if target_id is None:
                db.execute('DELETE FROM check_requests')
                db.execute('INSERT INTO check_requests VALUES (0)')
            elif not db.execute('SELECT 1 FROM check_requests WHERE target_id=0').fetchone():
                db.execute('INSERT OR IGNORE INTO check_requests VALUES (?)', (target_id,))

    def take_checks(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids = [row[0] for row in db.execute('SELECT target_id FROM check_requests ORDER BY target_id')]
            db.execute('DELETE FROM check_requests')
        return ids

    def has_checks(self):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM check_requests LIMIT 1').fetchone())

    def exclusions(self):
        with self.connect() as db:
            return {row['key']: row['title'] for row in db.execute('SELECT * FROM excluded_titles ORDER BY title')}

    def exclude(self, title):
        title = title.strip()
        key = title_key(title)
        if not key or len(title) > 500:
            raise ValueError('Укажите название книги (до 500 символов)')
        with self.connect() as db:
            db.execute('INSERT INTO excluded_titles VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET title=excluded.title', (key, title))

    def include(self, title):
        with self.connect() as db:
            return bool(db.execute('DELETE FROM excluded_titles WHERE key=?', (title_key(title),)).rowcount)

    def snapshot(self, target_id):
        with self.connect() as db:
            db.execute('BEGIN')
            target = self.target(db.execute('SELECT * FROM targets WHERE id=?', (target_id,)).fetchone())
            exclusions = frozenset(row[0] for row in db.execute('SELECT key FROM excluded_titles'))
        return target, target.price_limit if target else Decimal('0'), target.price_revision if target else 0, exclusions

    def needs_notification(self, target_id, book, eligible, revision):
        if not eligible:
            return False
        with self.connect() as db:
            row = db.execute('SELECT * FROM target_offers WHERE target_id=? AND sku=?', (target_id, book.sku)).fetchone()
        return row is None or not row['eligible'] or row['notified_price'] is None or row['price_revision'] != revision or book.card_price < Decimal(row['notified_price'])

    def record(self, target_id, book, eligible, revision, notified=False):
        with self.connect() as db:
            row = db.execute('SELECT notified_price,price_revision FROM target_offers WHERE target_id=? AND sku=?', (target_id, book.sku)).fetchone()
            price = str(book.card_price) if notified else (row[0] if row else None)
            # The revision tracks delivery, not merely checking a book under new settings.
            delivered_revision = revision if notified else (row[1] if row else revision)
            db.execute('''INSERT INTO target_offers(target_id,sku,eligible,notified_price,price_revision) VALUES(?,?,?,?,?)
                ON CONFLICT(target_id,sku) DO UPDATE SET eligible=excluded.eligible,
                notified_price=excluded.notified_price,price_revision=excluded.price_revision,checked_at=CURRENT_TIMESTAMP''',
                (target_id, book.sku, int(eligible), price, delivered_revision))
