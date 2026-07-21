import unittest
import tempfile
from pathlib import Path

from src.learning.recommendation_journal import RecommendationJournal
from src.workflow.daily_trading_assistant import DailyTradingAssistant
from src.workflow.stock_selection import classify_entry_timing


def classify(current=101, entry=100, stop=95, target=115, setup="BREAKOUT",
             breakout=True, confirmed=True, ema20=None, atr=None):
    return classify_entry_timing(
        current_price=current,
        levels={"entry": entry, "stop_loss": stop, "target_1": target, "resistance": entry},
        setup_category=setup, breakout_confirmed=breakout,
        entry_confirmed=confirmed, direction="BULLISH", ema20=ema20, atr=atr,
    )


class StockSelectionTests(unittest.TestCase):
    def test_buy_now_when_entry_is_confirmed_inside_valid_range(self):
        self.assertEqual(classify()["status"], "BUY NOW")

    def test_confirmed_entry_must_still_be_inside_atr_entry_zone(self):
        self.assertEqual(classify(current=98, entry=100, atr=4)["status"], "WAIT FOR BREAKOUT")
        self.assertEqual(classify(current=103, entry=100, atr=4)["status"], "WAIT FOR PULLBACK")

    def test_wait_for_breakout_when_confirmation_is_missing(self):
        result = classify(current=99, breakout=False, confirmed=False)
        self.assertEqual(result["status"], "WAIT FOR BREAKOUT")
        self.assertEqual(result["trigger_price"], 100)

    def test_wait_for_pullback_for_pullback_setup(self):
        result = classify(current=103, setup="BULLISH_PULLBACK", confirmed=False)
        self.assertEqual(result["status"], "WAIT FOR PULLBACK")

    def test_too_late_after_target_or_when_remaining_reward_is_poor(self):
        self.assertEqual(classify(current=115)["status"], "TOO LATE")
        self.assertEqual(classify(current=109, target=112)["status"], "TOO LATE")
        self.assertEqual(classify(current=108, target=130, ema20=100, atr=3)["status"],
                         "TOO LATE")

    def test_avoid_when_stop_is_invalidated_or_levels_are_missing(self):
        self.assertEqual(classify(current=94)["status"], "AVOID")
        result = classify_entry_timing(
            current_price=100, levels={}, setup_category="BREAKOUT",
            breakout_confirmed=False, entry_confirmed=False,
        )
        self.assertEqual(result["status"], "AVOID")

    def test_recent_journal_runs_support_stability_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = RecommendationJournal(Path(directory))
            journal.append("run-1", {"symbol": "SBIN", "final_action": "BUY"})
            journal.append("run-1", {"symbol": "TCS", "final_action": "REJECT"})
            journal.append("run-2", {"symbol": "RELIANCE", "final_action": "WATCHLIST"})
            runs = journal.recent_selected_symbols(2)
            self.assertEqual(runs[0], {"RELIANCE"})
            self.assertEqual(runs[1], {"SBIN"})

    def test_sector_limit_defers_lower_ranked_duplicate(self):
        def trade(symbol):
            return {
                "symbol": symbol, "sector": "BANKING", "status": "TRADE",
                "final_action": "BUY", "action": "BUY", "recommendation": "BUY",
                "trade_eligibility": {"eligible": True, "blocking_reasons": []},
                "final_decision": {"action": "BUY", "executable": True, "reasons": []},
                "risk": {"quantity": 10, "capital_used": 1000, "risk_amount": 50,
                         "actual_risk": 50},
                "entry_selection": {"status": "BUY NOW", "reason": "Ready"},
                "option_trade_approval": {"status": "REJECTED", "approved": False,
                                          "rejection_codes": []},
                "option_execution_valid": False, "option_context": {},
                "entry_confirmation": {"passed": True}, "option_structure": {},
                "event_risk": {}, "news": {},
            }
        reviewed = [trade("HDFCBANK"), trade("ICICIBANK"), trade("SBIN")]
        deferred = DailyTradingAssistant._apply_sector_limit(reviewed, 2)
        self.assertEqual(deferred, 1)
        self.assertEqual(reviewed[2]["status"], "WATCHLIST")
        self.assertEqual(reviewed[2]["risk"]["quantity"], 0)


if __name__ == "__main__":
    unittest.main()
