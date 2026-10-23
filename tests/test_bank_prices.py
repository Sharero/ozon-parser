import unittest
from decimal import Decimal
from ozon_monitor.bank_prices import select_ozon_price


class ExplicitBankPrices(unittest.TestCase):
    def test_ozon_offer_is_selected_instead_of_group_minimum(self):
        details = {'rows': [{'label': 'ОТП Банк', 'price': '499 ₽'},
                            {'label': 'Ozon Банк', 'price': '524 ₽'}]}
        self.assertEqual(select_ozon_price(details), Decimal('524'))

    def test_group_price_alone_is_not_ozon_price(self):
        self.assertIsNone(select_ozon_price({'rows': [{'label': 'С банками', 'price': '524 ₽'}]}))

    def test_empty_or_ambiguous_bank_price_is_not_accepted(self):
        self.assertIsNone(select_ozon_price({'rows': [{'label': 'Ozon Банк', 'price': '524 ₽ 579 ₽'}]}))
        self.assertIsNone(select_ozon_price({'snippets': [{'text': 'Ozon Банк'}]}))
        self.assertIsNone(select_ozon_price({'rows': [], 'membership': None}, '524 ₽'))

    def test_conflicting_ozon_offers_fail(self):
        with self.assertRaisesRegex(RuntimeError, 'противоречивые'):
            select_ozon_price({'rows': [{'label': 'Ozon Банк', 'price': '524 ₽'},
                                       {'label': 'Озон Банк', 'price': '529 ₽'}]})

    def test_duplicate_identical_labels_are_ok(self):
        self.assertEqual(select_ozon_price({'rows': [{'label': 'Ozon Банк', 'price': '524 ₽'},
                                                    {'label': 'С Ozon Картой', 'price': '524 ₽'}]}), Decimal('524'))

    def test_shared_price_requires_ozon_in_opened_bank_group_list(self):
        details = {'rows': [], 'membership': {'source': 'opened_bank_group_cell_list', 'bank': 'Ozon Банк'}}
        self.assertEqual(select_ozon_price(details, '524 ₽'), Decimal('524'))
        self.assertIsNone(select_ozon_price({'snippets': [{'text': 'Ozon Банк'}]}, '524 ₽'))
        self.assertIsNone(select_ozon_price({'membership': {'bank': 'Ozon Банк'}}, '524 ₽'))

    def test_individual_ozon_price_overrides_shared_group_price(self):
        details = {'rows': [{'label': 'Ozon Банк', 'price': '529 ₽'}],
                   'membership': {'source': 'opened_bank_group_cell_list', 'bank': 'Ozon Банк'}}
        self.assertEqual(select_ozon_price(details, '524 ₽'), Decimal('529'))
