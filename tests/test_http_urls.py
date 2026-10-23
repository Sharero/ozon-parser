import importlib.util
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch
from ozon_monitor.http_urls import ascii_url


class URLTests(unittest.TestCase):
    def test_unicode_query_is_utf8_percent_encoded(self):
        query = 'Стивен Кинг Король на все времена'
        result = ascii_url('https://www.ozon.ru/search/?text=' + query)
        result.encode('ascii')
        self.assertEqual(parse_qs(urlsplit(result).query)['text'], [query])

    def test_existing_escapes_not_double_encoded(self):
        url = 'https://www.ozon.ru/search/?text=%D0%9A%D0%B8%D0%BD%D0%B3&page=2'
        self.assertEqual(ascii_url(url), url)
        self.assertEqual(ascii_url(ascii_url(url)), url)

    def test_product_slug_and_delimiters(self):
        url = ascii_url('https://www.ozon.ru/product/оно-123/?a=1&b=2')
        self.assertIn('/product/%D0%BE%D0%BD%D0%BE-123/', url)
        self.assertTrue(url.endswith('?a=1&b=2'))


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('curl_cffi', 'aiohttp')),
                     'HTTP transport dependencies are required')
class TransportURLTests(unittest.TestCase):
    @patch('vendor.ozon_price_tracker.ozon_client.requests.get')
    def test_login_probe_and_unicode_urls_reach_http_client(self, get):
        from ozon_monitor.upstream_transport import PROBE, UpstreamTransport
        get.return_value = Mock(status_code=200, text='{"widgetStates": {}}')
        with tempfile.TemporaryDirectory() as directory:
            client = UpstreamTransport(Path(directory))
            for url in (PROBE,
                        'https://www.ozon.ru/search/?text=Стивен%20Кинг',
                        'https://www.ozon.ru/product/оно-123/'):
                self.assertEqual(client._make_request(url, cookies={'session': 'test'})[0], 200)
                call = get.call_args
                endpoint, = call.args
                endpoint.encode('ascii')
                for value in call.kwargs['headers'].values():
                    value.encode('latin-1')  # Same encoding that previously failed.
                referer = call.kwargs['headers']['Referer']
                nested_url = parse_qs(urlsplit(endpoint).query)['url'][0]
                parts = urlsplit(referer)
                self.assertEqual(nested_url, parts.path + ('?' + parts.query if parts.query else ''))
                self.assertEqual(call.kwargs['cookies'], {'session': 'test'})
