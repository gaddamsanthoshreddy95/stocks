import unittest

from src.options.models.option_chain import OptionChain
from src.options.models.option_contract import OptionContract
from src.options.trade_builder import OptionTradeBuilder


def contract(strike, option_type, premium):
    return OptionContract(
        "TEST", "2026-07-30", strike, option_type, premium, premium - .05, premium + .05,
        5000, 100000, 1000, 30, lot_size=50,
    )


class CreditSpreadBuilderTests(unittest.TestCase):
    def setUp(self):
        self.chain = OptionChain(
            "TEST", 100, "2026-07-30",
            calls=[contract(105, "CE", 4), contract(110, "CE", 2)],
            puts=[contract(90, "PE", 2), contract(95, "PE", 4)],
        )

    def test_bear_call_spread_has_sell_call_and_long_call_hedge(self):
        result = OptionTradeBuilder.build(
            self.chain, "Bear Call Spread", "BEARISH", resistance=105,
            risk_budget=10000, capital_available=10000,
        )
        self.assertTrue(result["available"])
        self.assertTrue(result["risk_defined"])
        self.assertEqual([(leg["side"], leg["strike"]) for leg in result["legs"]],
                         [("SELL", 105), ("BUY", 110)])
        self.assertGreater(result["net_credit"], 0)

    def test_bull_put_spread_has_sell_put_and_lower_put_hedge(self):
        result = OptionTradeBuilder.build(
            self.chain, "Bull Put Spread", "BULLISH", support=95,
            risk_budget=10000, capital_available=10000,
        )
        self.assertEqual([(leg["side"], leg["strike"]) for leg in result["legs"]],
                         [("SELL", 95), ("BUY", 90)])

    def test_iron_condor_is_four_leg_defined_risk_structure(self):
        result = OptionTradeBuilder.build(
            self.chain, "Iron Condor", "NEUTRAL", support=95, resistance=105,
            risk_budget=10000, capital_available=10000,
        )
        self.assertTrue(result["available"])
        self.assertEqual(len(result["legs"]), 4)
        self.assertEqual(result["structure_type"], "DEFINED_RISK_CREDIT_SPREAD")


if __name__ == "__main__":
    unittest.main()
