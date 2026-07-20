from dataclasses import replace
from datetime import date, timedelta
import unittest

from src.application.settings import PlatformSettings
from src.options.models.option_chain import OptionChain
from src.options.models.option_contract import OptionContract
from src.options.short_put.strike_selector import ShortPutStrikeSelector
from src.options.short_put.strategy_engine import ShortPutStrategyEngine


def contract(strike, delta=-.15, bid=2, ask=2.1, volume=500, oi=5000, iv=25):
    return OptionContract(
        f"TEST{strike}PE", "", strike, "PE", (bid + ask) / 2, bid, ask,
        volume, oi, 100, iv, delta=delta, lot_size=100,
    )


def fixture_chain(days=14):
    expiry = (date.today() + timedelta(days=days)).isoformat()
    puts = [contract(85, -.08, .9, 1), contract(90), contract(91, -.19), contract(95, -.3)]
    for put in puts:
        put.expiry = expiry
    return OptionChain("TEST", 100, expiry, puts=puts)


def bullish_analysis(category="PULLBACK", equity_rr=.4):
    return {
        "analysis": {"current_price": 100, "trend": "STRONG BULLISH", "ema20": 101,
                     "ema50": 98, "macd": 1, "macd_signal_line": .5,
                     "relative_volume": 1.2, "atr": 5},
        "entry": {"support": 95, "risk_reward": equity_rr, "atr": 5},
        "candlestick": {"signal": "BUY"}, "breakout": {"confirmed": False},
        "setup_evaluation": {"stage_1": {"category": category, "evidence": {"support_nearby": True}},
                             "stage_2": {"checks": {"bullish_reversal_candle": True,
                                                      "macd_above_signal": True,
                                                      "volume_above_1_2x": True}}},
    }


class ShortPutStrategyTests(unittest.TestCase):
    def setUp(self):
        self.settings = PlatformSettings()

    def test_eight_to_ten_percent_band(self):
        self.assertEqual(ShortPutStrikeSelector.strike_band(300, 8, 10), (270, 276))

    def test_selects_actual_strike_and_valid_expiry(self):
        selected, band, error = ShortPutStrikeSelector.select([fixture_chain()], 100, self.settings)
        self.assertIsNone(error)
        self.assertIn(selected[2].strike, {90, 91})
        self.assertEqual(band, (90, 92))

    def test_searches_all_band_strikes_before_rejecting_low_premium(self):
        chain = fixture_chain()
        chain.puts[1] = contract(90, delta=-.08, bid=.45, ask=.5)
        chain.puts[1].expiry = chain.expiry
        chain.puts[2] = contract(91, delta=-.16, bid=1.2, ask=1.3)
        chain.puts[2].expiry = chain.expiry

        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)

        self.assertEqual(plan["candidate"]["sold_put_strike"], 91)
        self.assertNotIn("PREMIUM_TOO_LOW", [item["code"] for item in plan["evaluation"]["rejections"]])
        self.assertEqual(plan["strike_search"]["evaluated"], 2)
        self.assertTrue(plan["strike_search"]["exhaustive_within_band"])
        self.assertEqual(len(plan["strike_search"]["evaluations"]), 2)
        self.assertTrue(all("probability_source" in row for row in plan["strike_search"]["evaluations"]))

    def test_sparse_band_adds_adjacent_listed_strikes(self):
        chain = fixture_chain()
        # Leave only 90 inside the 90-92 target band.
        chain.puts = [contract(85), contract(87.5), contract(90), contract(95), contract(97.5)]
        for put in chain.puts:
            put.expiry = chain.expiry
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)
        strikes = {row["strike"] for row in plan["strike_search"]["evaluations"]}
        self.assertEqual(strikes, {85, 87.5, 90, 95, 97.5})
        self.assertTrue(plan["strike_search"]["includes_adjacent_strikes"])

    def test_black_scholes_terminal_probability_is_reported_per_strike(self):
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [fixture_chain()], self.settings)
        evaluations = plan["strike_search"]["evaluations"]
        self.assertTrue(all(row["probability_otm"] is not None for row in evaluations))
        self.assertTrue(all(row["probability_source"] == "BLACK_SCHOLES" for row in evaluations))

    def test_bull_put_payoff_atr_support_and_lot_sizing(self):
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [fixture_chain()], self.settings)
        self.assertTrue(plan["available"])
        self.assertEqual(plan["strategy"], "BULL_PUT_SPREAD")
        self.assertLess(plan["candidate"]["sold_put_strike"], 95)
        self.assertGreaterEqual(plan["candidate"]["atr_coverage"], 1.5)
        self.assertEqual(plan["net_credit"], 1.0)
        self.assertEqual(plan["breakeven"], 89.0)
        self.assertGreater(plan["maximum_loss"], 0)
        self.assertGreater(plan["return_on_risk_percent"], 0)
        self.assertGreaterEqual(plan["lots"], 1)

    def test_cash_secured_put_payoff_and_breakeven(self):
        settings = replace(self.settings, short_put_allow_naked=True, short_put_prefer_credit_spread=False)
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [fixture_chain()], settings)
        self.assertTrue(plan["available"])
        self.assertEqual(plan["strategy"], "CASH_SECURED_PUT")
        self.assertEqual(plan["breakeven"], 88)
        self.assertEqual(plan["capital_required"], 99000)
        self.assertEqual(plan["maximum_loss"], 96800)
        self.assertEqual(plan["maximum_profit"], 2200)

    def test_strike_above_support_is_rejected(self):
        analysis = bullish_analysis()
        analysis["entry"]["support"] = 89
        plan = ShortPutStrategyEngine.evaluate("TEST", analysis, [fixture_chain()], self.settings)
        self.assertIn("STRIKE_ABOVE_SUPPORT", [item["code"] for item in plan["evaluation"]["rejections"]])

    def test_low_atr_coverage_is_rejected(self):
        analysis = bullish_analysis()
        analysis["analysis"]["atr"] = 10
        analysis["entry"]["atr"] = 10
        plan = ShortPutStrategyEngine.evaluate("TEST", analysis, [fixture_chain()], self.settings)
        self.assertIn("ATR_COVERAGE_TOO_LOW", [item["code"] for item in plan["evaluation"]["rejections"]])

    def test_wide_bid_ask_spread_is_rejected(self):
        chain = fixture_chain()
        for index in (1, 2):
            strike = chain.puts[index].strike
            chain.puts[index] = contract(strike, bid=1, ask=2)
            chain.puts[index].expiry = chain.expiry
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)
        self.assertIn("BID_ASK_SPREAD_TOO_WIDE", [item["code"] for item in plan["evaluation"]["rejections"]])

    def test_invalid_quotes_are_rejected(self):
        chain = fixture_chain()
        chain.puts[1] = contract(90, bid=0, ask=0)
        chain.puts[1].expiry = chain.expiry
        chain.puts[2] = contract(91, bid=0, ask=0)
        chain.puts[2].expiry = chain.expiry
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)
        self.assertFalse(plan["available"])
        self.assertIn("INVALID_QUOTES", [item["code"] for item in plan["evaluation"]["rejections"]])

    def test_missing_iv_and_delta_are_explicit(self):
        chain = fixture_chain()
        chain.puts[1] = contract(90, delta=None, iv=0)
        chain.puts[1].expiry = chain.expiry
        chain.puts[2] = contract(91, delta=None, iv=0)
        chain.puts[2].expiry = chain.expiry
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)
        codes = [item["code"] for item in plan["evaluation"]["rejections"]]
        self.assertIn("IV_UNAVAILABLE", codes)
        self.assertIn("GREEKS_UNAVAILABLE", codes)

    def test_no_valid_expiry(self):
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [fixture_chain(2)], self.settings)
        self.assertEqual(plan["rejection_code"], "NO_VALID_EXPIRY")

    def test_event_risk_rejected(self):
        plan = ShortPutStrategyEngine.evaluate(
            "TEST", bullish_analysis(), [fixture_chain()], self.settings,
            news={"events": ["probe"], "sentiment": "NEUTRAL"}, event_data_available=True,
        )
        self.assertEqual(plan["rejection_code"], "EVENT_RISK")

    def test_equity_rr_can_fail_while_short_put_is_approved(self):
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(equity_rr=.2), [fixture_chain()], self.settings)
        self.assertTrue(plan["available"])

    def test_oversold_bearish_stock_is_not_eligible(self):
        analysis = bullish_analysis("REVERSAL CANDIDATE")
        analysis["analysis"].update({"trend": "BEARISH", "current_price": 90, "ema50": 98,
                                     "macd": -.5, "macd_signal_line": 0, "relative_volume": .4})
        analysis["setup_evaluation"]["stage_2"]["checks"]["bullish_reversal_candle"] = False
        plan = ShortPutStrategyEngine.evaluate("TEST", analysis, [fixture_chain()], self.settings)
        self.assertEqual(plan["rejection_code"], "REVERSAL_NOT_CONFIRMED")

    def test_confirmed_bullish_pullback_is_eligible(self):
        self.assertTrue(ShortPutStrategyEngine.evaluate(
            "TEST", bullish_analysis("PULLBACK"), [fixture_chain()], self.settings
        )["available"])

    def test_delta_is_recomputed_from_available_iv(self):
        chain = fixture_chain()
        for index in (1, 2):
            chain.puts[index] = contract(chain.puts[index].strike, delta=None, iv=60)
            chain.puts[index].expiry = chain.expiry
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)
        self.assertEqual(plan["candidate"]["probability_source"], "BLACK_SCHOLES")
        self.assertIsNotNone(plan["candidate"]["delta"])

    def test_atr_probability_fallback_is_low_quality(self):
        chain = fixture_chain()
        for index in (1, 2):
            chain.puts[index] = contract(chain.puts[index].strike, delta=None, iv=0)
            chain.puts[index].expiry = chain.expiry
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [chain], self.settings)
        self.assertIsNone(plan["candidate"])
        self.assertEqual(plan["best_rejected_candidate"]["probability_source"], "ATR_FALLBACK")
        self.assertEqual(plan["best_rejected_candidate"]["probability_quality"], "LOW")
        self.assertIsNone(plan["strike_search"]["selected_strike"])

    def test_monthly_expiry_is_preferred_within_same_month(self):
        earlier, later = fixture_chain(14), fixture_chain(21)
        selected, _, _ = ShortPutStrikeSelector.select([earlier, later], 100, self.settings, 95, 5)
        self.assertEqual(selected[0].expiry, later.expiry)

    def test_market_and_sector_context_can_reject_approved_plan(self):
        plan = ShortPutStrategyEngine.evaluate("TEST", bullish_analysis(), [fixture_chain()], self.settings)
        contextual = ShortPutStrategyEngine.apply_context(
            plan, {"status": "CONFLICT"}, {"available": True, "score": 40},
            {"sentiment": "NEUTRAL", "events": []}, self.settings,
        )
        self.assertFalse(contextual["available"])
        self.assertEqual(contextual["rejection_code"], "MARKET_CONTEXT_CONFLICT")

    def test_portfolio_exposure_is_enforced(self):
        plan = ShortPutStrategyEngine.evaluate(
            "TEST", bullish_analysis(), [fixture_chain()], self.settings,
            exposure_context={"portfolio_exposure_percent": 30, "sector_exposure_percent": 0,
                              "correlated_exposure_percent": 0},
        )
        self.assertIn("PORTFOLIO_EXPOSURE_EXCEEDED",
                      [item["code"] for item in plan["evaluation"]["rejections"]])

    def test_injected_broker_margin_is_used(self):
        plan = ShortPutStrategyEngine.evaluate(
            "TEST", bullish_analysis(), [fixture_chain()], self.settings,
            exposure_context={"broker_margin_per_lot": 500, "broker_margin_available": True,
                              "portfolio_exposure_percent": 0, "sector_exposure_percent": 0,
                              "correlated_exposure_percent": 0},
        )
        self.assertEqual(plan["margin_source"], "BROKER_MARGIN")

    def test_injected_corporate_event_is_blocked(self):
        event_date = (date.today() + timedelta(days=5)).isoformat()
        plan = ShortPutStrategyEngine.evaluate(
            "TEST", bullish_analysis(), [fixture_chain()], self.settings,
            corporate_events=[{"date": event_date, "type": "EARNINGS"}],
        )
        self.assertEqual(plan["rejection_code"], "EVENT_RISK")


if __name__ == "__main__":
    unittest.main()
