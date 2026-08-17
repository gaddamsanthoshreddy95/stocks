import json
from datetime import date

from sensibull_results_calendar import load_result_dates, result_highlight, structure_calendar_rows


def test_calendar_date_is_inherited_and_only_stock_results_are_alerts(tmp_path):
    rows = structure_calendar_rows([
        {"raw_text": "14th Aug, Friday Alkem Ltd. (ALKEM) Stock Results (Q1)"},
        {"raw_text": "Bharat Dynamics Ltd. (BDL) Stock Results (Q1)"},
        {"raw_text": "HAL Ltd. (HAL) Dividend of ₹ 10 Per Share"},
    ], date(2026, 8, 17))
    assert rows[1]["event_date"] == "2026-08-14"
    assert rows[1]["symbol"] == "BDL"
    assert rows[2]["event_type"] == "DIVIDEND"

    cache = tmp_path / "calendar.json"
    cache.write_text(json.dumps({
        "requested_date": "2026-08-17", "records": rows,
    }), encoding="utf-8")
    assert load_result_dates(cache) == {"ALKEM": date(2026, 8, 14), "BDL": date(2026, 8, 14)}


def test_result_highlight_boundaries():
    dates = {
        "PAST": date(2026, 8, 16), "TODAY": date(2026, 8, 17),
        "FIFTEEN": date(2026, 9, 1), "LATER": date(2026, 9, 2),
    }
    as_of = date(2026, 8, 17)
    assert result_highlight("PAST", as_of, dates)["status"] == "RESULT DECLARED"
    assert result_highlight("TODAY", as_of, dates)["status"] == "RESULT DUE WITHIN 15 DAYS"
    assert result_highlight("FIFTEEN", as_of, dates)["status"] == "RESULT DUE WITHIN 15 DAYS"
    assert result_highlight("LATER", as_of, dates)["status"] == "RESULT LATER"
