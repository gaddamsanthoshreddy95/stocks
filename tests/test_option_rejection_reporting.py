import unittest

from src.options.models.option_chain import OptionChain
from src.options.models.option_contract import OptionContract
from src.options.trade_builder import OptionTradeBuilder
from src.options.entry_validator import OptionEntryValidator


def contract(volume=20_000, oi=20_000, bid=9.9, ask=10.1, lot=100):
    return OptionContract("TEST", "2026-07-30", 100, "CE", 10, bid, ask,
                          volume, oi, 0, 25, lot_size=lot)


class OptionRejectionReportingTests(unittest.TestCase):
    def test_no_liquid_contract_has_specific_code(self):
        chain = OptionChain("TEST", 100, "2026-07-30", calls=[contract(volume=10)])
        result = OptionTradeBuilder.build(chain, "Long Call", "BULLISH", risk_budget=10_000)
        self.assertEqual(result["rejection"]["code"], "NO_LIQUID_CONTRACTS")
        self.assertEqual(result["strategy"], "Wait")
        self.assertEqual(result["legs"], [])
        self.assertFalse(OptionEntryValidator.validate(chain, result, "BULLISH")["approved"])

    def test_risk_budget_rejection_reports_required_and_available(self):
        chain = OptionChain("TEST", 100, "2026-07-30", calls=[contract(lot=100)])
        result = OptionTradeBuilder.build(chain, "Long Call", "BULLISH", risk_budget=500)
        self.assertEqual(result["rejection"]["code"], "RISK_BUDGET_EXCEEDED")
        self.assertGreater(result["rejection"]["required_budget"], result["rejection"]["available_budget"])
