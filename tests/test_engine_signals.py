from datetime import datetime, timedelta
from unittest import TestCase

from engine import DEFAULT, recommend


class PriceSignals(TestCase):
    def calculate(self, early, recent, stock, sellable=None, unavailable=0):
        now = datetime(2026, 11, 25)
        sales = [
            dict(date=str(now.date() - timedelta(days=30 - index)),
                 available=index >= unavailable, units=(early if index < 15 else recent)
                 if index >= unavailable else 0)
            for index in range(30)
        ]
        product = dict(stock=stock, sellable=stock if sellable is None else sellable,
                       sales=sales, cost=200, supplies=[])
        return recommend(product, dict(price=1000, anchor=1000), DEFAULT, now)

    def test_faster_sales_with_limited_free_stock(self):
        result = self.calculate(1, 3, 240, sellable=30)
        self.assertEqual((result['kind'], result['step'], result['recent_rate']), ('up', 2, 3))
        self.assertIn('Темп продаж вырос', result['reason'])

    def test_slowdown_is_not_used_with_incomplete_availability(self):
        slow = self.calculate(4, 1, 130)
        self.assertEqual(slow['kind'], 'down')
        self.assertIn('Темп продаж снизился', slow['reason'])
        partial = self.calculate(4, 1, 30, unavailable=11)
        self.assertEqual((partial['previous_rate'], partial['unavailable'], partial['kind']), (None, 11, 'same'))

    def test_zero_free_stock_and_small_unsold_stock_do_not_generate_actions(self):
        self.assertEqual(self.calculate(4, 1, 130, sellable=0)['kind'], 'same')
        self.assertEqual(self.calculate(0, 0, 9)['kind'], 'same')
        self.assertEqual(self.calculate(0, 0, 10)['kind'], 'down')
