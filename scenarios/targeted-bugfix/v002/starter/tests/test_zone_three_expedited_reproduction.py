import unittest

from src.shipping_rules import shipping_quote


class ZoneThreeExpeditedReproductionTests(unittest.TestCase):
    def test_two_kg_expedited_zone_three_quote_includes_surcharge(self):
        self.assertEqual(shipping_quote(2, 3, expedited=True), 19.2)


if __name__ == "__main__":
    unittest.main()
