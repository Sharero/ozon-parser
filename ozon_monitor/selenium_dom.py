"""Run the existing visible-page parser through Selenium without calling JSON API."""
import logging
import time
from .ozon import Ozon
from .search_dom import describe_search

log = logging.getLogger(__name__)


class DOMModeSelected(Exception):
    """The transport switched from JSON to an established visible browser session."""


class SeleniumLocator:
    def __init__(self, driver, selector, pattern=None):
        self.driver, self.selector, self.pattern = driver, selector, pattern

    def elements(self):
        elements = self.driver.find_elements('css selector', self.selector)
        if self.pattern is not None:
            elements = [element for element in elements if self.pattern.search(element.text)]
        return elements

    def count(self):
        return len(self.elements())

    def inner_text(self):
        elements = self.elements()
        return elements[0].text if elements else ''

    def filter(self, *, has_text):
        return SeleniumLocator(self.driver, self.selector, has_text)

    def evaluate_all(self, expression):
        return self.driver.execute_script('return (' + expression + ')(arguments[0]);', self.elements())


class SeleniumPage:
    def __init__(self, driver):
        self.driver = driver

    def goto(self, url, **_):
        self.driver.get(url)
        return None

    def locator(self, selector):
        return SeleniumLocator(self.driver, selector)

    def evaluate(self, expression):
        if expression.strip().startswith(('() =>', 'async ')):
            return self.driver.execute_script('return (' + expression + ')();')
        return self.driver.execute_script('return (' + expression + ');')

    def wait_for_timeout(self, milliseconds):
        time.sleep(milliseconds / 1000)

    def wait_for_function(self, expression, timeout=30000):
        from selenium.webdriver.support.ui import WebDriverWait
        WebDriverWait(self.driver, timeout / 1000).until(
            lambda _: self.driver.execute_script('return Boolean((' + expression + ')());'))

    def wait_for_selector(self, selector, timeout=30000):
        from selenium.webdriver.support.ui import WebDriverWait
        WebDriverWait(self.driver, timeout / 1000).until(lambda _: self.locator(selector).count() > 0)


class SeleniumDOMOzon(Ozon):
    def __init__(self, driver, max_pages=100, delay=3):
        super().__init__(SeleniumPage(driver), max_pages, delay)
        self.warmed_up = True

    def json_offer(self, url, sku):
        # Explicitly avoid the endpoint already known to return 403.
        return None

    def confirm_search(self, url):
        self.goto(url)
        snapshot = self.wait_search(timeout=15)
        if not snapshot.get('links'):
            raise RuntimeError('В HTML-выдаче нет книг: ' + describe_search(snapshot))
        log.info('Обычная HTML-выдача Selenium доступна: %s', describe_search(snapshot))
        return True

    def confirm_product(self, url):
        self.goto(url)
        self.page.wait_for_selector('h1', timeout=15000)
        title = self.page.locator('h1').inner_text().strip()
        if not title:
            raise RuntimeError('HTML-карточка книги не распознана')
        return True
