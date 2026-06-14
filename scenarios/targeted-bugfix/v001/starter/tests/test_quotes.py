import unittest

from src.shipping_rules import shipping_quote


class ShippingQuoteTests(unittest.TestCase):
    def test_standard_zone_three_quote(self):
        self.assertEqual(shipping_quote(2, 3), 11.7)

    def test_expedited_zone_two_quote(self):
        self.assertEqual(shipping_quote(2, 2, expedited=True), 16.27)


if __name__ == "__main__":
    unittest.main()
