import json
import unittest
from decimal import Decimal
from unittest.mock import Mock
from ozon_monitor.price_json import card_offer
from ozon_monitor.ozon import Ozon
from ozon_monitor.browser import automation_options, prepare_context


class JsonPrices(unittest.TestCase):
    def payload(self, **changes):
        state = {'link': '/modal?product_id=123', 'cardPrice': '399,99 ₽',
                 'price': '450 ₽', 'isAvailable': True, 'lexemes': {'withOzonCard': 'с Ozon Картой'}}
        state.update(changes)
        return {'widgetStates': {'webPrice-1': json.dumps(state)}}

    def test_explicit_card_price(self):
        self.assertEqual(card_offer(self.payload(), '123'), (Decimal('399.99'), True))

    def test_no_regular_price_substitution(self):
        self.assertEqual(card_offer(self.payload(cardPrice=None, price='299 ₽'), '123'), (None, True))

    def test_other_sku_and_missing_label_rejected(self):
        self.assertIsNone(card_offer(self.payload(), '456'))
        self.assertIsNone(card_offer(self.payload(lexemes={}), '123'))

    def test_unknown_availability_rejected(self):
        self.assertIsNone(card_offer(self.payload(isAvailable='true'), '123'))

    def test_conflicting_widgets_fail(self):
        payload = self.payload()
        payload['widgetStates']['webPrice-2'] = self.payload(cardPrice='299 ₽')['widgetStates']['webPrice-1']
        with self.assertRaises(RuntimeError):
            card_offer(payload, '123')

    def test_unavailable_offer(self):
        self.assertEqual(card_offer(self.payload(isAvailable=False), '123'), (Decimal('399.99'), False))

    def test_malformed_and_challenge_not_price(self):
        self.assertIsNone(card_offer({'challengeURL': '/challenge'}, '123'))
        self.assertIsNone(card_offer({'widgetStates': {'webPrice-1': 'invalid'}}, '123'))


class BrowserBootstrap(unittest.TestCase):
    def test_startup_configuration(self):
        options = automation_options()
        self.assertIn('--enable-automation', options['ignore_default_args'])
        self.assertIn('--disable-blink-features=AutomationControlled', options['args'])
        context = Mock()
        prepare_context(context)
        context.add_init_script.assert_called_once()

    def test_challenge_can_complete_without_refresh(self):
        page = Mock()
        page.goto.return_value.status = 403
        page.locator.return_value.inner_text.side_effect = ['Похоже, нет соединения', 'Книги']
        page.locator.return_value.count.return_value = 1
        Ozon(page, delay=0).goto('https://www.ozon.ru/')
        page.goto.assert_called_once()

    def test_stuck_challenge_fails(self):
        page = Mock()
        page.goto.return_value.status = 403
        page.locator.return_value.inner_text.return_value = 'Похоже, нет соединения fab_chlg_example'
        page.locator.return_value.count.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'fab_chlg_example'):
            Ozon(page, delay=0).goto('https://www.ozon.ru/')
