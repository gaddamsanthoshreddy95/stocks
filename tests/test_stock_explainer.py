import unittest

from src.ui.stock_explainer import explain_stock_question


class StockExplainerTests(unittest.TestCase):
    def test_explains_trade_from_report_evidence(self):
        report = {
            "run_id": "run-1", "generated_at": "2026-07-22T09:00:00+00:00",
            "trades": [{
                "symbol": "IDEA", "status": "TRADE", "final_action": "BUY",
                "selection_reason": "Trend and entry confirmation passed.",
                "quality_score": 81, "execution_readiness_score": 78,
                "levels": {"entry": 9.5, "stop_loss": 9.2, "target_1": 10.1,
                           "risk_reward": 2},
                "adverse_move_risk": {
                    "probability_stays_above_adverse_barrier": 84,
                    "probability_target_before_adverse_barrier": 67,
                    "probability_no_overnight_gap_beyond_barrier": 93,
                },
            }], "watchlist": [], "rejected": [],
        }
        answer = explain_stock_question(report, "why did you suggest idea?")
        self.assertIn("executable TRADE", answer)
        self.assertIn("Quality score: 81.0/100", answer)
        self.assertIn("target before barrier: 67.0%", answer)
        self.assertIn("run-1", answer)

    def test_does_not_call_rejected_stock_a_suggestion(self):
        report = {"run_id": "run-2", "trades": [], "watchlist": [], "rejected": [{
            "symbol": "IDEA", "status": "REJECTED", "final_action": "REJECT",
            "rejection_reasons": ["Relative strength failed."],
        }]}
        answer = explain_stock_question(report, "Explain IDEA")
        self.assertIn("not suggested for execution", answer)
        self.assertIn("Relative strength failed", answer)

    def test_requests_symbol_when_question_has_none(self):
        report = {"trades": [{"symbol": "IDEA"}], "watchlist": [], "rejected": []}
        self.assertIn("include one stock symbol", explain_stock_question(report, "why?"))


if __name__ == "__main__":
    unittest.main()
