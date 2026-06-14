import unittest

from src.pricing import apply_discount


class ExistingPricingTests(unittest.TestCase):
    def test_fixed_discount(self):
        self.assertEqual(apply_discount(20, {"kind": "fixed", "value": 5}), 15)


if __name__ == "__main__":
    unittest.main()
