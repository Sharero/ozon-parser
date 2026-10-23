import sqlite3
from decimal import Decimal
from pathlib import Path
from .models import Book


class State:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS offers (
            sku TEXT PRIMARY KEY, eligible INTEGER NOT NULL,
            notified_price TEXT, checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        self.db.commit()

    def needs_notification(self, book: Book) -> bool:
        row = self.db.execute('SELECT eligible, notified_price FROM offers WHERE sku=?', (book.sku,)).fetchone()
        return book.eligible and (row is None or not row[0] or row[1] is None or book.card_price < Decimal(row[1]))

    def record(self, book: Book, notified: bool = False):
        # Call only after successful delivery when a notification was needed.
        row = self.db.execute('SELECT notified_price FROM offers WHERE sku=?', (book.sku,)).fetchone()
        price = str(book.card_price) if notified else (row[0] if row else None)
        with self.db:
            self.db.execute('''INSERT INTO offers(sku, eligible, notified_price) VALUES (?, ?, ?)
                ON CONFLICT(sku) DO UPDATE SET eligible=excluded.eligible,
                notified_price=excluded.notified_price, checked_at=CURRENT_TIMESTAMP''',
                (book.sku, int(book.eligible), price))

    def close(self):
        self.db.close()
