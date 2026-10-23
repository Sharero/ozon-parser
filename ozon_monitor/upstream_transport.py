"""Adapter for the vendored noobby91 client (MIT); cookies stay in DATA_DIR."""
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from vendor.ozon_price_tracker.ozon_client import OzonClient
from .diagnostics import ozon_access_error, check_age_confirmation, AgeConfirmationRequired
from .http_urls import ascii_url
from .search_config import search_urls
from .network import proxy_url
from .selenium_dom import DOMModeSelected, SeleniumDOMOzon

log = logging.getLogger(__name__)
PROBE = 'https://www.ozon.ru/search/?' + urlencode({'text': 'Стивен Кинг Король на все времена'})


class UpstreamTransport(OzonClient):
    def __init__(self, directory: Path, probe_url=None, probe_kind='series'):
        self.directory = directory
        self.cookie_file = directory / 'ozon_cookies.json'
        super().__init__()
        self.refresh_attempted = False
        self.driver = None
        self.mode = 'json'
        self.probe_url = probe_url or search_urls(['Стивен Кинг Король на все времена'])[0]
        self.probe_kind = probe_kind
        self.proxy = proxy_url(os.getenv('OZON_PROXY_URL', '').strip())

    def _build_api_headers(self, product_url):
        return super()._build_api_headers(ascii_url(product_url))

    def _make_request(self, url, cookies=None):
        # Use the same encoded URL for both the nested endpoint URL and Referer.
        if not self.proxy:
            return super()._make_request(ascii_url(url), cookies=cookies)
        from curl_cffi import requests
        try:
            response = requests.get(self._build_api_url(ascii_url(url)), impersonate='chrome124',
                                    headers=self._build_api_headers(url),
                                    cookies=self._cookies if cookies is None else cookies,
                                    proxy=self.proxy, timeout=20, allow_redirects=True)
            return response.status_code, response.text
        except Exception as exc:
            log.warning('Запрос Ozon через прокси не выполнен: %s', type(exc).__name__)
            return 0, ''

    def close(self):
        driver, self.driver = self.driver, None
        self.mode = 'json'
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                log.warning('Не удалось корректно закрыть Selenium')

    def _browser_request(self, driver, url):
        if urlsplit(driver.current_url).hostname not in ('ozon.ru', 'www.ozon.ru'):
            raise RuntimeError('Selenium находится вне Ozon; JSON-запрос не отправлен')
        endpoint = self._build_api_url(ascii_url(url))
        if not endpoint:
            raise RuntimeError('Некорректный адрес JSON Ozon')
        parts = urlsplit(endpoint)
        relative_endpoint = parts.path + '?' + parts.query
        driver.set_script_timeout(30)
        result = driver.execute_async_script("""
            const endpoint = arguments[0], done = arguments[arguments.length - 1];
            const abort = new AbortController();
            const timer = setTimeout(() => abort.abort(), 20000);
            fetch(endpoint, {credentials: 'same-origin', signal: abort.signal,
                headers: {'Accept': 'application/json', 'x-o3-app-name': 'dweb_client'}})
                .then(async response => done({status: response.status, text: await response.text()}))
                .catch(() => done({status: 0, text: ''}))
                .finally(() => clearTimeout(timer));
        """, relative_endpoint)
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise RuntimeError('Selenium вернул некорректный результат JSON-запроса')
        return int(result.get('status', 0)), result['text']

    def _search_confirmed(self, status, body, channel):
        widgets = self._parse_widget_states(body) if status == 200 and not self._is_captcha_response(status, body) else None
        names = sorted({key.split('-')[0] for key in (widgets or {})})
        log.info('Проверка JSON через %s: HTTP %s; виджеты: %s', channel, status, ', '.join(names[:15]) or 'нет')
        if self.probe_kind == 'book':
            return bool(widgets and any(key.startswith('webProductHeading-') for key in widgets))
        return bool(widgets and any(key.startswith('searchResultsV2-') for key in widgets))

    def _probe(self, driver, candidate):
        status, body = self._make_request(self.probe_url, cookies=candidate)
        if self._search_confirmed(status, body, 'curl_cffi'):
            self.mode = 'json'
            return False
        log.info('Проверяю тот же JSON из открытого Selenium, без переноса cookies в другой HTTP-клиент')
        try:
            browser_status, browser_body = self._browser_request(driver, self.probe_url)
        except Exception as exc:
            log.warning('JSON-запрос внутри Selenium не завершён: %s', type(exc).__name__)
            browser_status, browser_body = 0, ''
        if self._search_confirmed(browser_status, browser_body, 'браузер Selenium'):
            self.mode = 'browser-json'
            log.info('JSON доступен только в Selenium; браузер останется открытым на время мониторинга')
            return True
        log.info('Оба JSON-способа отказали. Проверяю обычную HTML-страницу поиска в Selenium')
        try:
            dom = SeleniumDOMOzon(driver, delay=0)
            if self.probe_kind == 'book':
                dom.confirm_product(self.probe_url)
            else:
                dom.confirm_search(self.probe_url)
        except AgeConfirmationRequired:
            raise
        except Exception as exc:
            detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            raise RuntimeError(f'Поиск недоступен: curl_cffi HTTP {status}, Selenium fetch HTTP {browser_status}; '
                               f'HTML-поиск: {detail}. Cookies не сохранены.') from None
        self.mode = 'dom'
        log.info('Выбран HTML-режим: поиск и карточки будут читаться из Selenium без JSON API')
        return True

    def _load_cookies_from_file(self):
        try:
            data = json.loads(self.cookie_file.read_text())
            cookies = data['cookies']
            if not isinstance(cookies, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in cookies.items()):
                raise ValueError('invalid cookies')
            self._cookies = cookies
            self._cookies_loaded_at = datetime.fromisoformat(data['loaded_at'])
        except (OSError, ValueError, KeyError, TypeError):
            log.info('Потребуется получение cookies через Selenium')

    def _save_cookies_to_file(self):
        temporary = self.cookie_file.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump({'cookies': self._cookies, 'loaded_at': datetime.now().isoformat()}, stream)
        temporary.replace(self.cookie_file)

    def refresh(self, interactive=False, retain_browser=False, setup_url='https://www.ozon.ru/'):
        # Validate before launching a browser or allowing input of account details.
        if interactive:
            parts = urlsplit(setup_url)
            if (parts.scheme != 'https' or parts.hostname not in ('ozon.ru', 'www.ozon.ru')
                    or parts.username or parts.password or parts.port not in (None, 443)):
                raise RuntimeError('--login принимает только HTTPS-ссылку на ozon.ru без логина и пароля')
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium_stealth import stealth

        self.refresh_attempted = True
        self.close()
        options = Options()
        if self.proxy:
            options.add_argument('--proxy-server=' + self.proxy.replace('socks5h://', 'socks5://', 1))
        if os.getenv('CHROMIUM_NO_SANDBOX', 'false').lower() == 'true':
            options.add_argument('--no-sandbox')
        binary = os.getenv('CHROMIUM_EXECUTABLE_PATH') or shutil.which('chromium') or shutil.which('chromium-browser') or shutil.which('google-chrome')
        if binary:
            options.binary_location = binary
        for argument in ('--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled',
                         '--disable-extensions', '--disable-plugins', '--window-size=1920,1080',
                         f'--user-data-dir={self.directory / "selenium-browser"}'):
            options.add_argument(argument)
        if not interactive and os.getenv('HEADLESS', 'false').lower() == 'true':
            options.add_argument('--headless=new')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        driver_path = os.getenv('CHROMEDRIVER_PATH') or shutil.which('chromedriver')
        log.info('Получаю cookies через Selenium + selenium-stealth')
        try:
            driver = webdriver.Chrome(service=Service(executable_path=driver_path) if driver_path else Service(), options=options)
        except Exception as exc:
            raise RuntimeError('Selenium не запустил Chromium. Проверьте совпадение версий chromium/chromedriver, '
                               'CHROMIUM_EXECUTABLE_PATH и CHROMEDRIVER_PATH. Тип ошибки: ' + type(exc).__name__) from None
        try:
            driver.set_page_load_timeout(60)
            # Native Linux platform instead of the reference project's hardcoded Win32.
            stealth(driver, languages=['ru-RU', 'ru'], vendor='Google Inc.', platform='Linux x86_64',
                    webgl_vendor='Intel Inc.', renderer='Intel Iris OpenGL Engine', fix_hairline=True)
            driver.get(setup_url if interactive else 'https://www.ozon.ru/')
            if interactive:
                input('В окне Selenium выберите регион, при необходимости войдите и подтвердите '
                      'возраст на сайте своими данными. Дождитесь закрытия окна подтверждения. '
                      'Затем нажмите Enter здесь для проверки и сохранения сессии: ')
            else:
                for second in range(45):
                    text = driver.find_element('tag name', 'body').text
                    if driver.get_cookies() and not ozon_access_error(text):
                        break
                    if second % 10 == 0:
                        log.info('Ожидаю доступ Ozon: %s/45 секунд', second)
                    time.sleep(1)
            text = driver.find_element('tag name', 'body').text
            check_age_confirmation(text)
            error = ozon_access_error(text)
            if error:
                raise RuntimeError(error)
            candidate = {item['name']: item['value'] for item in driver.get_cookies()}
            if not candidate:
                raise RuntimeError('Selenium не получил cookies Ozon')
            # Check the same endpoint through both network stacks before declaring failure.
            keep_browser = self._probe(driver, candidate) or retain_browser
            self._cookies = {item['name']: item['value'] for item in driver.get_cookies()}
            self._cookies_loaded_at = datetime.now()
            self._save_cookies_to_file()
            if keep_browser:
                self.driver = driver
            log.info('Доступ к поиску подтверждён; режим=%s; cookies сохранены', self.mode)
        finally:
            if self.driver is not driver:
                self.mode = 'json'
                driver.quit()

    def page(self, url: str) -> dict:
        if self.mode == 'dom':
            raise DOMModeSelected()
        if self.driver is None and self._cookies_expired():
            if self.refresh_attempted:
                raise RuntimeError('Cookies Ozon не получены; повторное открытие браузера в этом обходе отключено')
            self.refresh()
        for attempt in range(2):
            if self.mode == 'dom':
                raise DOMModeSelected()
            status, body = self._browser_request(self.driver, url) if self.driver is not None else self._make_request(url)
            blocked = self._is_captcha_response(status, body) or bool(ozon_access_error(body))
            if blocked and attempt == 0 and not self.refresh_attempted:
                self.refresh()
                continue
            if blocked:
                raise RuntimeError(f'Ozon отклонил JSON-запрос (HTTP {status}); Selenium + curl_cffi не восстановили доступ')
            if status != 200:
                raise RuntimeError(f'JSON Ozon HTTP {status}; состояние книги не изменено')
            try:
                payload = json.loads(body)
            except ValueError:
                raise RuntimeError('Ozon вернул не JSON; состояние книги не изменено') from None
            if not isinstance(payload, dict) or not isinstance(payload.get('widgetStates'), dict):
                raise RuntimeError('Ozon вернул JSON без widgetStates')
            return payload
        raise RuntimeError('Не удалось получить JSON Ozon')
