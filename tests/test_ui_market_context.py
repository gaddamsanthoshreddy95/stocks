import unittest
from datetime import datetime, timezone

import pandas as pd

from ui_app import (candidate_rows, likely_news_reaction, news_impact, news_rows,
                    data_age, decision_checks, decision_timeline, display_date,
                    candidate_history_rows, opportunity_groups, option_leg_rows, outcome_rows,
                    price_figure, report_changes,
                    portfolio_snapshot, primary_blocker, rejection_summary, snapshot_rows,
                    trade_override_required, trade_performance, _trade_status)


class UIMarketContextTests(unittest.TestCase):
    def test_position_dates_and_timestamp_ages_are_compact(self):
        self.assertEqual(display_date("2026-07-31"), "31 Jul 2026")
        now = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(data_age("2026-07-22T09:55:00+00:00", now), "5m ago")

    def test_decision_checks_separate_execution_gates(self):
        checks = decision_checks({
            "status": "TRADE", "selection_status": "BUY NOW",
            "news": {"news_state": "ANALYZED"},
            "event_risk": {"hard_block": False}, "risk": {"quantity": 10},
        })
        self.assertTrue(all(item["passed"] for item in checks))

    def test_opportunity_groups_are_mutually_exclusive(self):
        candidates = [
            {"symbol": "BUY", "status": "TRADE", "selection_status": "BUY NOW"},
            {"symbol": "WAIT", "status": "WATCHLIST", "final_action": "WAIT_FOR_CONFIRMATION"},
            {"symbol": "WATCH", "status": "WATCHLIST"},
            {"symbol": "BLOCK", "status": "WATCHLIST", "final_action": "WAIT",
             "_opportunity_source": "REJECTED"},
        ]
        groups = opportunity_groups(candidates)
        symbols = [item["symbol"] for items in groups.values() for item in items]
        self.assertCountEqual(symbols, ["BUY", "WAIT", "WATCH", "BLOCK"])
        self.assertEqual(len(symbols), len(set(symbols)))
        self.assertEqual(groups["No trade / blocked"][0]["symbol"], "BLOCK")

    def test_rejections_are_grouped_by_actionable_primary_blocker(self):
        rejected = [
            {"symbol": "A", "selection_reason": "Risk/reward 1.1 is below minimum"},
            {"symbol": "B", "reasons": ["Risk reward failed"]},
            {"symbol": "C", "selection_reason": "Entry confirmation missing"},
        ]
        self.assertEqual(primary_blocker(rejected[0]), "Insufficient risk/reward")
        summary = rejection_summary(rejected)
        self.assertEqual(summary[0]["Count"], 2)
        self.assertEqual(summary[0]["Symbols"], "A, B")

    def test_portfolio_snapshot_includes_unrealized_risk_and_overdue_reviews(self):
        trades = [{"symbol": "SBIN", "status": "OPEN", "side": "BUY", "quantity": 10,
                   "entry_price": 100, "stop_loss": 95, "fees": 5,
                   "hold_until": "2020-01-01"}]
        snapshot = portfolio_snapshot(trades, {"SBIN": 110})
        self.assertEqual(snapshot["capital_deployed"], 1000)
        self.assertEqual(snapshot["risk_at_stops"], 50)
        self.assertEqual(snapshot["unrealized_pnl"], 95)
        self.assertEqual(snapshot["overdue_reviews"], 1)

    def test_candidate_history_tracks_saved_report_progression(self):
        reports = [
            {"date": "2026-07-21", "watchlist": [{"symbol": "SBIN", "quality_score": 70}]},
            {"date": "2026-07-22", "trades": [{"symbol": "SBIN", "status": "TRADE",
                                                 "quality_score": 85}]},
        ]
        rows = candidate_history_rows(reports, "SBIN")
        self.assertEqual([row["Quality"] for row in rows], [70, 85])
        self.assertEqual(rows[-1]["Status"], "TRADE")

    def test_non_approved_candidates_require_discretionary_override(self):
        self.assertFalse(trade_override_required({"status": "TRADE", "final_action": "BUY"}))
        self.assertTrue(trade_override_required({"status": "WATCHLIST",
                                                 "final_action": "WAIT_FOR_CONFIRMATION"}))
        self.assertTrue(trade_override_required({"status": "REJECTED", "final_action": "REJECT"}))

    def test_short_equity_position_profits_down_and_loses_up(self):
        trade = {"entry_price": 100, "quantity": 10, "side": "SELL", "fees": 0,
                 "stop_loss": 105, "target_price": 90}
        down = _trade_status(trade, 90)
        up = _trade_status(trade, 105)
        self.assertEqual(down["pnl"], 100)
        self.assertEqual(down["decision"], "EXIT / BOOK PROFIT")
        self.assertEqual(up["pnl"], -50)
        self.assertEqual(up["decision"], "EXIT")

    def test_news_impact_labels_cover_positive_and_negative_strength(self):
        self.assertEqual(news_impact(72), "SUPER POSITIVE")
        self.assertEqual(news_impact(20), "POSITIVE")
        self.assertEqual(news_impact(3), "NEUTRAL")
        self.assertEqual(news_impact(-20), "NEGATIVE")
        self.assertEqual(news_impact(-72), "SUPER NEGATIVE")

    def test_news_rows_show_each_headline_and_likely_reaction(self):
        report = {"trades": [{"symbol": "SBIN", "news": {
            "score": 40, "materiality": "MEDIUM",
            "headlines": [{"title": "SBIN profit beats estimates", "source": "Example",
                           "published": "2026-07-20T10:00:00+00:00"}],
            "article_assessments": [{
                "title": "SBIN profit beats estimates", "materiality": "HIGH",
                "probabilities": {"positive": 82, "negative": 5, "neutral": 13},
            }],
        }}]}
        rows = news_rows(report)
        self.assertEqual(rows[0]["Stock"], "SBIN")
        self.assertEqual(rows[0]["Impact"], "SUPER POSITIVE")
        self.assertIn("buying interest", rows[0]["Likely stock reaction"])
        self.assertIn("upward", likely_news_reaction("SUPER POSITIVE", "HIGH"))

    def test_snapshot_rows_preserve_global_quote_status(self):
        rows = snapshot_rows({"sp500_futures": {"price": 6000, "change": -10,
                                                 "change_percent": -.17}})
        self.assertEqual(rows[0]["Market"], "Sp500 Futures")
        self.assertEqual(rows[0]["Status"], "AVAILABLE")

    def test_missing_price_is_unavailable(self):
        rows = snapshot_rows({"nikkei": {"price": None}})
        self.assertEqual(rows[0]["Status"], "UNAVAILABLE")

    def test_candidate_rows_expose_support_and_resistance(self):
        rows = candidate_rows({"trades": [{
            "symbol": "SBIN", "status": "TRADE",
            "levels": {"support": 790, "resistance": 825},
        }]})
        self.assertEqual(rows[0]["Support"], 790)
        self.assertEqual(rows[0]["Resistance"], 825)
        self.assertEqual(rows[0]["Executable trade"], "YES")

    def test_watchlist_candidate_is_not_presented_as_executable(self):
        rows = candidate_rows({"watchlist": [{"symbol": "PREMIERENE",
                                               "status": "WATCHLIST", "levels": {}}]})
        self.assertEqual(rows[0]["Executable trade"], "NO")

    def test_option_leg_rows_expose_exact_trade_strikes(self):
        rows = option_leg_rows({"option_strategy": {"trade": {"legs": [{
            "side": "BUY", "quantity": 1, "strike": 800,
            "option_type": "PE", "premium": 14.5,
        }]}}})
        self.assertEqual(rows[0]["Strike"], 800)
        self.assertEqual(rows[0]["Type"], "PE")

    def test_trade_performance_calculates_journal_statistics(self):
        result = trade_performance([
            {"status": "CLOSED", "realized_pnl": 200, "entry_price": 100,
             "stop_loss": 95, "quantity": 10},
            {"status": "CLOSED", "realized_pnl": -100, "entry_price": 50,
             "stop_loss": 45, "quantity": 10},
            {"status": "OPEN", "realized_pnl": None},
        ])
        self.assertEqual(result["closed"], 2)
        self.assertEqual(result["win_rate"], 50)
        self.assertEqual(result["net_pnl"], 100)
        self.assertEqual(result["expectancy"], 50)

    def test_price_figure_contains_price_volume_and_rsi(self):
        index = pd.date_range("2026-01-01", periods=60)
        frame = pd.DataFrame({
            "Open": range(100, 160), "High": range(102, 162),
            "Low": range(98, 158), "Close": range(101, 161),
            "Volume": range(1000, 1060),
        }, index=index)
        figure = price_figure(frame, "SBIN", {"entry": 150, "stop_loss": 140})
        names = {trace.name for trace in figure.data}
        self.assertTrue({"SBIN", "20 DMA", "50 DMA", "Volume", "RSI 14"}.issubset(names))

    def test_report_changes_identifies_new_removed_and_status_changes(self):
        previous = {"trades": [{"symbol": "SBIN", "status": "TRADE", "quality_score": 80}],
                    "watchlist": [{"symbol": "INFY", "status": "WATCHLIST"}]}
        current = {"watchlist": [{"symbol": "SBIN", "status": "WATCHLIST", "quality_score": 75}],
                   "trades": [{"symbol": "TCS", "status": "TRADE"}]}
        changes = {row["Symbol"]: row["Change"] for row in report_changes(current, previous)}
        self.assertEqual(changes, {"INFY": "REMOVED", "SBIN": "STATUS CHANGED", "TCS": "NEW"})

    def test_decision_timeline_and_outcome_attribution(self):
        timeline = decision_timeline({"symbol": "SBIN", "status": "TRADE", "quality_score": 80,
                                      "entry_selection": {"status": "BUY NOW"}})
        self.assertEqual(timeline[-1]["State"], "PASSED")
        outcomes = outcome_rows([{"symbol": "SBIN", "status": "CLOSED", "side": "BUY",
                                  "entry_price": 100, "exit_price": 110, "quantity": 10,
                                  "stop_loss": 95, "realized_pnl": 100, "strategy": "Swing"}])
        self.assertEqual(outcomes[0]["R multiple"], 2)
        self.assertEqual(outcomes[0]["Outcome"], "PROFIT")


if __name__ == "__main__":
    unittest.main()
