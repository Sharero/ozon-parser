"""Run with installed Playwright Chromium or CHROMIUM_EXECUTABLE_PATH."""
import os
import unittest
from playwright.sync_api import sync_playwright
from ozon_monitor.ozon import EXTRACT


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "RUN_BROWSER_TESTS=1 для браузерных тестов")
class ProductDOM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(
                executable_path=os.getenv('CHROMIUM_EXECUTABLE_PATH') or None)
        except Exception:
            cls.playwright.stop()
            raise
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def extract(self, price_html):
        self.page.set_content('''<h1>Оно</h1><dl>
            <dt>Автор</dt><dd>Кинг Стивен</dd>
            <dt>Серия</dt><dd>Король на все времена</dd>
            <dt>Тип обложки</dt><dd>Твёрдый переплёт</dd></dl>
            <div data-widget="webAddToCart"><button>В корзину</button></div>'''
            + price_html)
        return self.page.evaluate(EXTRACT)

    def test_only_labeled_card_price(self):
        data = self.extract('''<div data-widget="webPrice">
            <div><b>399 ₽</b><span>с Ozon Картой</span></div>
            <div><b>499 ₽</b><span>без Ozon Карты</span></div></div>''')
        self.assertEqual(data['price'], '399 ₽')
        self.assertEqual(data['fields']['Серия'], 'Король на все времена')
        self.assertTrue(data['available'])

    def test_regular_price_is_never_substituted(self):
        data = self.extract('<div data-widget="webPrice"><b>299 ₽</b></div>')
        self.assertIsNone(data['price'])

    def test_ambiguous_card_price_rejected(self):
        data = self.extract('''<div data-widget="webPrice"><div>
            <b>299 ₽</b><b>399 ₽</b><span>с Ozon Картой</span></div></div>''')
        self.assertIsNone(data['price'])

    def test_recommendation_price_ignored(self):
        data = self.extract('<div><b>199 ₽</b><span>с Ozon Картой</span></div>')
        self.assertIsNone(data['price'])

    def test_tile_grid_layout_without_search_results_widget(self):
        from ozon_monitor.search_dom import SEARCH_SNAPSHOT
        self.page.set_content('<main><div data-widget="tileGridDesktop">'
                              '<a href="https://www.ozon.ru/product/book-123/">Книга</a>'
                              '</div></main>')
        result = self.page.evaluate(SEARCH_SNAPSHOT)
        self.assertEqual(result['links'], ['https://www.ozon.ru/product/book-123/'])

    def test_main_content_fallback_excludes_navigation(self):
        from ozon_monitor.search_dom import SEARCH_SNAPSHOT
        self.page.set_content('<nav><a href="https://www.ozon.ru/product/nav-999/">Меню</a></nav>'
                              '<main><a href="https://www.ozon.ru/product/book-123/">Книга</a>'
                              '<a href="https://example.com/product/foreign-456/">Чужой сайт</a></main>')
        result = self.page.evaluate(SEARCH_SNAPSHOT)
        self.assertEqual(result['links'], ['https://www.ozon.ru/product/book-123/'])

    def test_latin_c_and_add_to_cart_button(self):
        self.page.set_content('<h1>Оно</h1><div data-widget="webPrice">'
                              '<div><b>499 ₽</b><span>c Ozon Картой</span></div></div>'
                              '<div data-widget="webAddToCart"><button>Добавить в корзину</button></div>')
        data = self.page.evaluate(EXTRACT)
        self.assertEqual(data['price'], '499 ₽')
        self.assertIs(data['available'], True)

    def test_unknown_purchase_button_is_not_confirmed_out_of_stock(self):
        self.page.set_content('<h1>Оно</h1><div data-widget="webPrice"></div>')
        self.assertIsNone(self.page.evaluate(EXTRACT)['available'])

    def test_full_characteristics_override_variant_options(self):
        self.page.set_content('<h1>Оно</h1><div><span>Тип обложки</span>'
                              '<span>Мягкая обложка Твердый переплет</span></div>'
                              '<section data-widget="webCharacteristics"><dl>'
                              '<dt>Тип переплета</dt><dd>Твердый переплет</dd>'
                              '</dl></section>')
        data = self.page.evaluate(EXTRACT)
        self.assertEqual(data['fields']['Тип обложки'], 'Твердый переплет')

    def test_selected_cover_only_not_all_variants(self):
        self.page.set_content('<h1>Оно</h1><div role="option" aria-selected="false">Мягкая обложка</div>'
                              '<div role="option" aria-selected="true">Твердый переплет</div>')
        self.assertEqual(self.page.evaluate(EXTRACT)['fields']['Тип обложки'], 'Твердый переплет')

    def test_real_green_mile_bank_group_is_not_direct_card_price(self):
        from pathlib import Path
        fixture = Path(__file__).parent / 'fixtures' / 'green_mile_webprice.html'
        self.page.set_content('<h1>Зеленая миля</h1>' + fixture.read_text())
        data = self.page.evaluate(EXTRACT)
        self.assertIsNone(data['price'])
        self.assertEqual(data['bank_group_price'], '524 ₽')

    def test_ozon_modal_layout_bank_group_membership(self):
        from ozon_monitor.bank_prices import OPEN_BANK_DETAILS, READ_BANK_DETAILS, select_ozon_price
        self.page.set_content('<div data-widget="webPrice"><button><span>524 ₽</span>'
                              '<span>С банками</span></button></div>')
        self.assertTrue(self.page.evaluate(OPEN_BANK_DETAILS))
        self.page.evaluate("""() => document.body.insertAdjacentHTML('beforeend',
            '<div data-widget="modalLayout"><h2>Банки со скидкой</h2><div data-widget="cellList">' +
            '<div><span>Ozon Банк</span></div><div>Другой банк</div></div><button>Понятно</button></div>')""")
        details = self.page.evaluate(READ_BANK_DETAILS)
        from decimal import Decimal
        self.assertEqual(select_ozon_price(details, '524 ₽'), Decimal('524'))

    def test_ozon_modal_layout_individual_price(self):
        from ozon_monitor.bank_prices import OPEN_BANK_DETAILS, READ_BANK_DETAILS, select_ozon_price
        self.page.set_content('<div data-widget="webPrice"><button><span>524 ₽</span>'
                              '<span>С банками</span></button></div>')
        self.page.evaluate(OPEN_BANK_DETAILS)
        self.page.evaluate("""() => document.body.insertAdjacentHTML('beforeend',
            '<div data-widget="modalLayout"><div data-widget="cellList">' +
            '<div><span>Ozon Банк</span><span>529 ₽</span></div>' +
            '<div><span>Другой банк</span><span>524 ₽</span></div></div></div>')""")
        from decimal import Decimal
        self.assertEqual(select_ozon_price(self.page.evaluate(READ_BANK_DETAILS), '524 ₽'), Decimal('529'))
