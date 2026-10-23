import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from ozon_monitor.diagnostics import browser_error_message, ozon_access_error
from ozon_monitor.telegram import Telegram


class Diagnostics(unittest.TestCase):
    def test_browser_errors_are_actionable(self):
        for text, expected in [
            ("Executable doesn't exist at /private/path", 'playwright install chromium'),
            ('Operation not permitted', 'вне ограниченной среды'),
            ('Missing X server or $DISPLAY', 'HEADLESS=true'),
            ('ProcessSingleton', 'уже используется'),
        ]:
            self.assertIn(expected, browser_error_message(RuntimeError(text)))

    @patch('ozon_monitor.telegram.urlopen')
    def test_check_reads_bot_and_chat_without_sending(self, urlopen):
        urlopen.return_value.__enter__.side_effect = [
            io.BytesIO(json.dumps({'ok': True, 'result': {'id': 1}}).encode()),
            io.BytesIO(json.dumps({'ok': True, 'result': {'id': 2}}).encode()),
        ]
        Telegram('SECRET', '123').check()
        methods = [call.args[0].full_url.rsplit('/', 1)[-1] for call in urlopen.call_args_list]
        self.assertEqual(methods, ['getMe', 'getChat'])

    @patch('ozon_monitor.telegram.urlopen')
    def test_credential_errors_do_not_expose_token(self, urlopen):
        for code, expected in [(401, 'токен'), (400, 'chat ID'), (403, 'Разблокируйте')]:
            urlopen.side_effect = HTTPError('https://example/SECRET', code, 'err', {}, io.BytesIO(b'{}'))
            with self.assertRaises(RuntimeError) as caught:
                Telegram('SECRET', '123').check()
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn('SECRET', str(caught.exception))


class OzonAccess(unittest.TestCase):
    def test_age_prompt_has_manual_setup_instructions(self):
        for text in ('Укажите дату рождения', 'Введите вашу дату рождения', 'Подтвердите свой возраст'):
            self.assertIn('--login', ozon_access_error(text))
        self.assertIsNone(ozon_access_error('Стивен Кинг 18+ Дата рождения автора'))

    def test_incident_recognized_even_with_http_200(self):
        text = 'Похоже, нет соединения Инцидент: fab_chlg_20261020164842_01M5D3E5PC6JY9W68PXGZFZC8C'
        for status in (200, 403):
            error = ozon_access_error(text, status)
            self.assertIn('fab_chlg_20261020164842_01M5D3E5PC6JY9W68PXGZFZC8C', error)
            self.assertIn('Цены недоступны', error)

    def test_normal_page_and_generic_http_error(self):
        self.assertIsNone(ozon_access_error('Книги Стивена Кинга'))
        self.assertIn('HTTP 403', ozon_access_error('', 403))
