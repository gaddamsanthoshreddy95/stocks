from src.sector.sector_strength import SectorStrength
from src.workflow.daily_trading_assistant import DailyTradingAssistant


def test_sector_adjustments_are_small_and_symmetric():
    assert SectorStrength.ranking_adjustment({"available": True, "rating": "STRONG"}) == 6
    assert SectorStrength.ranking_adjustment({"available": True, "rating": "BULLISH"}) == 3
    assert SectorStrength.ranking_adjustment({"available": True, "rating": "NEUTRAL"}) == 0
    assert SectorStrength.ranking_adjustment({"available": True, "rating": "WEAK"}) == -3
    assert SectorStrength.ranking_adjustment({"available": True, "rating": "VERY_WEAK"}) == -6


def test_concentrated_leadership_caps_bonus_and_missing_data_is_neutral():
    assert SectorStrength.ranking_adjustment({
        "available": True, "rating": "STRONG", "concentrated_leadership": True,
    }) == 2
    assert SectorStrength.ranking_adjustment({"available": False, "rating": "STRONG"}) == 0


def test_intrinsic_stock_score_can_outrank_strong_sector_stock():
    exceptional = 90 + SectorStrength.ranking_adjustment({
        "available": True, "rating": "WEAK",
    })
    average = 78 + SectorStrength.ranking_adjustment({
        "available": True, "rating": "STRONG",
    })
    assert exceptional > average


def test_sector_table_keeps_sectors_without_candidates_visible():
    rows = DailyTradingAssistant._rank_sectors(
        [{"sector": "IT", "ai_score": 80}],
        {
            "IT": {"available": True, "status": "AVAILABLE", "score": 70},
            "AUTO": {"available": True, "status": "AVAILABLE", "score": 85},
        },
    )
    by_sector = {row["sector"]: row for row in rows}
    assert by_sector["AUTO"]["candidate_count"] == 0
    assert by_sector["AUTO"]["ranking_basis"] == "SECTOR_MARKET_ONLY"
    assert by_sector["IT"]["candidate_count"] == 1
