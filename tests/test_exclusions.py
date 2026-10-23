import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from ozon_monitor.exclusions import title_key, load_exclusions
from test_monitor import sample


class PurchasedBooks(unittest.TestCase):
    def test_titles_match_case_yo_punctuation_and_ozon_author_suffix(self):
        self.assertEqual(title_key('«Зелёная миля»'), title_key('Зеленая миля | Кинг Стивен'))
        self.assertEqual(title_key('Темная башня: Стрелок'), title_key('Темная башня. Стрелок | Стивен Кинг'))

    def test_short_titles_are_not_substrings(self):
        self.assertNotEqual(title_key('Оно'), title_key('Ночная смена'))
        self.assertNotEqual(title_key('Оно'), title_key('Оно. Ловец снов'))

    def test_file_comments_bom_duplicates_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'books.txt'
            path.write_text('\ufeff# Комментарий\n\nСияние\nСИЯНИЕ | Кинг Стивен\n', encoding='utf-8')
            self.assertEqual(load_exclusions(path), frozenset({'сияние'}))
            path.write_text('Оно\n', encoding='utf-8')
            self.assertEqual(load_exclusions(path), frozenset({'оно'}))

    def test_excluded_title_cannot_send_or_change_notification_history(self):
        from main import run_cycle
        source, state, telegram = Mock(), Mock(), Mock()
        source.discover.return_value = [sample().url]
        source.book.return_value = replace(sample(), title='Оно | Кинг Стивен')
        with self.assertLogs('monitor', level='INFO') as logs:
            self.assertTrue(run_cycle(source, state, telegram, frozenset({'оно'})))
        telegram.send.assert_not_called()
        state.record.assert_not_called()
        state.needs_notification.assert_not_called()
        self.assertIn('исключено=1', '\n'.join(logs.output))

    def test_another_title_still_notifies(self):
        from main import run_cycle
        source, state, telegram = Mock(), Mock(), Mock()
        source.discover.return_value = [sample().url]
        source.book.return_value = sample()
        state.needs_notification.return_value = True
        self.assertTrue(run_cycle(source, state, telegram, frozenset({'сияние'})))
        telegram.send.assert_called_once_with(sample())
