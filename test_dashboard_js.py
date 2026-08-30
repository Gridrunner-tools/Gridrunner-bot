import unittest
from main import DASHBOARD


class TestDashboardJs(unittest.TestCase):
    def test_active_strategies_stop_button_onclick_is_valid(self):
        # Regression: a malformed onclick here was a JS SyntaxError that aborted
        # the ENTIRE dashboard script, so price/balance polling, the Start
        # button, and the Active Strategies cards all went dead.
        self.assertNotIn("stopStrategy('' + sid + '')", DASHBOARD)
        self.assertIn("stopStrategy('${sid}')", DASHBOARD)


def test_dashboard_js_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDashboardJs)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("dashboard JS unit tests failed")


if __name__ == "__main__":
    unittest.main()
