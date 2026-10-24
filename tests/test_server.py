import tempfile
import threading
import time
import unittest
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
    @patch('ozon_monitor.service.Monitor')
    def test_changes_during_scan_queue_one_target_without_full_rescan(self, monitor):
        calls = []
        def check(target_ids=None):
            calls.append(target_ids)
            if len(calls) == 1:
                self.store.request_check(1)
                self.store.request_check(1)
            else:
                self.stop.set()
        monitor.return_value.run_all.side_effect = check
        with patch('ozon_monitor.service.BotPoller.run'):
            run_service(self.store, Mock(), self.path, 12345, self.stop, interval=3600)
        self.assertEqual(calls, [None, [1]])

    @patch('ozon_monitor.service.Monitor')
    def test_manual_check_does_not_shift_hourly_schedule(self, monitor):
        deadlines = []
        def check(target_ids=None):
            deadlines.append(self.store.get('next_check'))
            if len(deadlines) == 1:
                self.store.request_check(1)
            else:
                self.stop.set()
        monitor.return_value.run_all.side_effect = check
        with patch('ozon_monitor.service.BotPoller.run'):
            run_service(self.store, Mock(), self.path, 12345, self.stop, interval=3600)
        self.assertEqual(deadlines[0], deadlines[1])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.store = Store(self.path / 'db.sqlite3')
        self.store.initialize()
        self.stop = threading.Event()

    def tearDown(self):
        self.tmp.cleanup()

    @patch('ozon_monitor.service.BotPoller.run')
    @patch('ozon_monitor.service.Monitor')
    def test_checks_repeat_without_concurrent_workers(self, monitor, poller):
        starts = []
        def check():
            starts.append(time.monotonic())
            if len(starts) == 3:
                self.stop.set()
        monitor.return_value.run_all.side_effect = check
        run_service(self.store, Mock(), self.path, 12345, self.stop, interval=0.025)
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(starts[1] - starts[0], 0.02)
        self.assertEqual(self.store.get('worker_state'), 'сервис остановлен')

    @patch('ozon_monitor.service.Monitor')
    def test_owner_commands_are_processed_while_worker_is_blocked(self, monitor):
        started, changed = threading.Event(), threading.Event()
        def check():
            started.set()
            self.assertTrue(changed.wait(2))
            self.assertEqual(self.store.snapshot(1)[1], 400)
            self.stop.set()
        def poll(poller):
            if not started.wait(2):
                self.stop.set()
                return
            poller.controller.handle({'message': {'from': {'id': 12345}, 'chat': {'id': 12345}, 'text': '/price 1 400'}})
            changed.set()
        monitor.return_value.run_all.side_effect = check
        with patch('ozon_monitor.service.BotPoller.run', poll):
            run_service(self.store, Mock(), self.path, 12345, self.stop)
        self.assertTrue(changed.is_set())
