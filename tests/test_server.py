import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from ozon_monitor.network import proxy_url
from ozon_monitor.service import run_service
from ozon_monitor.store import Store


class NetworkConfiguration(unittest.TestCase):
    def test_proxy_schemes_and_invalid_credentials(self):
        self.assertEqual(proxy_url('socks5h://127.0.0.1:1080'), 'socks5h://127.0.0.1:1080')
        self.assertEqual(proxy_url(''), '')
        for value in ('http://user:password@host:3128', 'http://host:0', 'https://host:99999', 'ftp://host:123', 'http://host:3128/path'):
            with self.assertRaises(ValueError):
                proxy_url(value)
        with self.assertRaises(ValueError):
            proxy_url('socks5://host:1080', socks=False)

    @patch('vendor.ozon_price_tracker.ozon_client.requests.get')
    @patch.dict('os.environ', {'OZON_PROXY_URL': 'socks5://127.0.0.1:1080'})
    def test_ozon_http_requests_use_explicit_proxy(self, get):
        from ozon_monitor.upstream_transport import UpstreamTransport, PROBE
        with tempfile.TemporaryDirectory() as directory:
            transport = UpstreamTransport(Path(directory))
            get.return_value = Mock(status_code=200, text='{}')
            transport._make_request(PROBE)
        self.assertEqual(get.call_args.kwargs['proxy'], 'socks5://127.0.0.1:1080')


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.store = Store(self.path / 'db.sqlite3')
        self.store.initialize()
        self.current = datetime(2026, 10, 25, 10, 29, 59)
        self.stop = threading.Event()
        def advance(seconds):
            self.current += timedelta(seconds=seconds)
            return self.stop.is_set()
        self.stop.wait = advance

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, monitor):
        with patch('ozon_monitor.service.Monitor', return_value=monitor), \
             patch('ozon_monitor.service.BotPoller.run'), \
             patch('ozon_monitor.service.DailyReporter.run'):
            run_service(self.store, Mock(), self.path, 12345, self.stop, now=lambda: self.current)

    def test_start_and_regular_commands_wait_for_boundary(self):
        monitor = Mock()
        starts = []
        self.store.request_check(1)
        def check(**kwargs):
            starts.append((self.current, kwargs))
            if len(starts) == 1:
                self.store.request_check(1)
            else:
                self.stop.set()
        monitor.run_all.side_effect = check
        self.service(monitor)
        self.assertEqual(starts, [(datetime(2026, 10, 25, 10, 30), {}),
                                  (datetime(2026, 10, 25, 11, 0), {})])

    def test_explicit_check_is_immediate_without_shifting_schedule(self):
        monitor = Mock()
        starts = []
        self.store.request_check(1, immediate=True)
        def check(**kwargs):
            starts.append((self.current, kwargs, self.store.get('next_check')))
            if len(starts) == 2:
                self.stop.set()
        monitor.run_all.side_effect = check
        self.service(monitor)
        self.assertEqual(starts[0][:2], (datetime(2026, 10, 25, 10, 29, 59), {'target_ids': [1]}))
        self.assertEqual(starts[1][:2], (datetime(2026, 10, 25, 10, 30), {}))
        self.assertEqual(starts[0][2], '25.10.2026 10:30:00')

    def test_long_run_skips_missed_slots_without_overlap(self):
        monitor = Mock()
        starts = []
        def check():
            starts.append(self.current)
            if len(starts) == 1:
                self.current = datetime(2026, 10, 25, 11, 12)
            else:
                self.stop.set()
        monitor.run_all.side_effect = check
        self.service(monitor)
        self.assertEqual(starts, [datetime(2026, 10, 25, 10, 30), datetime(2026, 10, 25, 11, 30)])
