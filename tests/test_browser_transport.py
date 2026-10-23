import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from ozon_monitor.upstream_transport import UpstreamTransport
from ozon_monitor.diagnostics import AgeConfirmationRequired


SEARCH = json.dumps({'widgetStates': {'searchResultsV2-1': json.dumps({'items': []})}})


class BrowserTransport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = UpstreamTransport(Path(self.tmp.name))
        self.driver = Mock()
        self.driver.current_url = 'https://www.ozon.ru/'
        fallback = patch('ozon_monitor.upstream_transport.SeleniumDOMOzon')
        self.dom = fallback.start()
        self.addCleanup(fallback.stop)
        self.dom.return_value.confirm_search.side_effect = RuntimeError('HTML выдача недоступна')

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_age_gate_in_dom_probe_preserves_error_type(self):
        self.dom.return_value.confirm_search.side_effect = AgeConfirmationRequired('Подтвердите возраст')
        with patch.object(self.client, '_make_request', return_value=(403, 'blocked')), \
                patch.object(self.client, '_browser_request', return_value=(403, 'blocked')):
            with self.assertRaises(AgeConfirmationRequired):
                self.client._probe(self.driver, {'session': 'test'})

    def test_curl_success_does_not_require_browser_fetch(self):
        with patch.object(self.client, '_make_request', return_value=(200, SEARCH)), \
                patch.object(self.client, '_browser_request') as fetch:
            self.assertFalse(self.client._probe(self.driver, {'session': 'test'}))
        fetch.assert_not_called()

    def test_curl_403_with_working_browser_selects_browser(self):
        with patch.object(self.client, '_make_request', return_value=(403, 'blocked')), \
                patch.object(self.client, '_browser_request', return_value=(200, SEARCH)):
            self.assertTrue(self.client._probe(self.driver, {'session': 'test'}))

    def test_both_403_report_both_statuses(self):
        with patch.object(self.client, '_make_request', return_value=(403, 'blocked')), \
                patch.object(self.client, '_browser_request', return_value=(403, 'blocked')):
            with self.assertRaisesRegex(RuntimeError, 'curl_cffi HTTP 403, Selenium fetch HTTP 403'):
                self.client._probe(self.driver, {'session': 'test'})
        self.assertFalse(self.client.cookie_file.exists())

    def test_http_200_without_search_is_not_success(self):
        unknown = json.dumps({'widgetStates': {'other-1': '{}'}})
        with patch.object(self.client, '_make_request', return_value=(200, unknown)), \
                patch.object(self.client, '_browser_request', return_value=(200, unknown)):
            with self.assertRaisesRegex(RuntimeError, 'HTML-поиск'):
                self.client._probe(self.driver, {})

    def test_browser_mode_does_not_use_curl_or_expired_cookie_file(self):
        self.client.driver = self.driver
        self.client._cookies_loaded_at = datetime.now() - timedelta(hours=24)
        self.driver.execute_async_script.return_value = {'status': 200, 'text': SEARCH}
        with patch.object(self.client, '_make_request') as curl, patch.object(self.client, 'refresh') as refresh:
            self.assertIn('widgetStates', self.client.page('https://www.ozon.ru/search/?text=Кинг'))
        curl.assert_not_called()
        refresh.assert_not_called()
        endpoint = self.driver.execute_async_script.call_args.args[1]
        self.assertTrue(endpoint.startswith('/api/entrypoint-api.bx/page/json/v2?'))
        endpoint.encode('ascii')

    def test_browser_challenge_does_not_become_empty_catalog(self):
        self.client.driver = self.driver
        self.client.refresh_attempted = True
        self.driver.execute_async_script.return_value = {'status': 403, 'text': 'blocked'}
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            self.client.page('https://www.ozon.ru/search/')

    def test_foreign_browser_page_cannot_send_request(self):
        self.driver.current_url = 'https://example.com/'
        with self.assertRaisesRegex(RuntimeError, 'вне Ozon'):
            self.client._browser_request(self.driver, 'https://www.ozon.ru/')
        self.driver.execute_async_script.assert_not_called()

    def test_close_releases_driver_once(self):
        self.client.driver = self.driver
        self.client.close()
        self.client.close()
        self.driver.quit.assert_called_once()

    def test_blocked_json_with_working_html_selects_dom(self):
        self.dom.return_value.confirm_search.side_effect = None
        self.dom.return_value.confirm_search.return_value = True
        with patch.object(self.client, '_make_request', return_value=(403, 'blocked')), \
                patch.object(self.client, '_browser_request', return_value=(403, 'blocked')):
            self.assertTrue(self.client._probe(self.driver, {'session': 'test'}))
        self.assertEqual(self.client.mode, 'dom')
        self.dom.return_value.confirm_search.assert_called_once()

    def test_dom_mode_never_calls_json_endpoint(self):
        from ozon_monitor.selenium_dom import DOMModeSelected
        self.client.mode = 'dom'
        self.client.driver = self.driver
        with patch.object(self.client, '_make_request') as curl, patch.object(self.client, '_browser_request') as fetch:
            with self.assertRaises(DOMModeSelected):
                self.client.page('https://www.ozon.ru/search/')
        curl.assert_not_called()
        fetch.assert_not_called()
