import unittest


class StarterSmokeTests(unittest.TestCase):
    def test_starter_import_path_is_ready(self):
        import src.alerts

        self.assertIsNotNone(src.alerts)


if __name__ == "__main__":
    unittest.main()
