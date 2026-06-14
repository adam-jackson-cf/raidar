import unittest


class StarterSmokeTests(unittest.TestCase):
    def test_starter_import_path_is_ready(self):
        import src.ledger_utils

        self.assertIsNotNone(src.ledger_utils)


if __name__ == "__main__":
    unittest.main()
