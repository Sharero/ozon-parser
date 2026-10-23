import unittest
from unittest.mock import Mock, call
from main import run_cycle
from test_monitor import sample


class PriceCutoff(unittest.TestCase):
    def source(self, books):
        source, state, telegram = Mock(), Mock(), Mock()
        source.discover.return_value = [f'url-{i}' for i in range(len(books))]
        source.book.side_effect = books
        state.needs_notification.side_effect = lambda book: book.eligible
        return source, state, telegram

    def test_stops_at_first_over_threshold_without_reading_later_books(self):
        source, state, telegram = self.source([sample('499'), sample('550.01'), sample('399')])
        with self.assertLogs('monitor', level='INFO') as logs:
            self.assertTrue(run_cycle(source, state, telegram))
        self.assertEqual(source.book.call_args_list, [call('url-0'), call('url-1')])
        telegram.send.assert_called_once_with(sample('499'))
        state.record.assert_any_call(sample('550.01'))
        self.assertIn('прекращаю обход', '\n'.join(logs.output))
        self.assertIn('проверено=2', '\n'.join(logs.output))

    def test_equal_threshold_does_not_stop_or_notify(self):
        source, state, telegram = self.source([sample('550'), sample('499')])
        self.assertTrue(run_cycle(source, state, telegram))
        self.assertEqual(source.book.call_count, 2)
        telegram.send.assert_called_once_with(sample('499'))

    def test_excluded_expensive_book_still_stops_without_history_change(self):
        source, state, telegram = self.source([sample('551'), sample('499')])
        self.assertTrue(run_cycle(source, state, telegram, frozenset({'оно'})))
        source.book.assert_called_once()
        state.record.assert_not_called()
        telegram.send.assert_not_called()

    def test_unreadable_price_does_not_stop_following_books(self):
        source, state, telegram = self.source([RuntimeError('unknown price'), sample('499')])
        self.assertFalse(run_cycle(source, state, telegram))
        self.assertEqual(source.book.call_count, 2)
        telegram.send.assert_called_once_with(sample('499'))
