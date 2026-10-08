"""Fact-based explanations for approved and rejected futures research candidates."""

class FuturesReportPresenter:
    @staticmethod
    def price_range(history, price):
        import pandas as pd
        if history is None or history.empty:
            return {}
        end = history.index.max()
        start = end - pd.Timedelta(weeks=52)
        window = history.loc[history.index >= start]
        if history.index.min() > start + pd.Timedelta(days=7):
            return {"reason": "Insufficient history for a verified 52-week range"}
        high, low = float(window.High.max()), float(window.Low.min())
        return {"high": high, "low": low, "as_of": str(end),
                "change_from_high_percent": (price / high - 1) * 100 if high > 0 else None,
                "rise_from_low_percent": (price / low - 1) * 100 if low > 0 else None}

    @staticmethod
    def rsi_label(value):
        if value is None:
            return "Not verified"
        if value < 30:
            return "Oversold"
        if value < 45:
            return "Weak"
        if value <= 55:
            return "Neutral"
        if value <= 70:
            return "Bullish momentum"
        return "Overbought"

    @staticmethod
    def number(value, suffix=""):
        return "Not verified" if value is None else f"{value:,.2f}{suffix}"

    @classmethod
    def explain(cls, item):
        selection = item.get("futures_selection") or {}
        checks = selection.get("checks", {})
        factor = lambda name: checks.get(name, {}).get("factors", {})
        n = cls.number
        rows = []
        technical = item.get("technical") or {}
        rows.append("Why reviewed: " + (item.get("discovery_reason") or "Technical/discovery shortlist; this does not mean all futures requirements passed.") )
        rows.append("Final decision: " + item.get("selection_reason", item.get("final_action", "Not verified")))
        contract = selection.get("execution_contract") or {}
        if contract:
            rows.append(f"Execution contract: {contract.get('tradingsymbol')}; expiry {contract.get('expiry')}; lot size {contract.get('lot_size')}.")
        for key, value in checks.items():
            if key.startswith("futures_"):
                values = ", ".join(f"{name}: {n(number)}" for name, number in value.get("factors", {}).items())
                rows.append(f"{key.removeprefix('futures_').replace('_', ' ').title()}: {value.get('status', 'UNKNOWN')}. "
                            f"{values}. Reason: {', '.join(value.get('reason_codes', []))}.")
        rows.append(f"Trend: {technical.get('trend', 'Not verified')}; momentum: {technical.get('momentum', 'Not verified')}; "
                    f"RSI: {n(technical.get('rsi'))} ({cls.rsi_label(technical.get('rsi'))}); relative volume: {n(technical.get('relative_volume'), 'x')}.")
        price_range = technical.get("price_range") or {}
        rows.append(f"52-week high / low: ₹{n(price_range.get('high'))} / ₹{n(price_range.get('low'))}; "
                    f"change from high: {n(price_range.get('change_from_high_percent'), '%')}; "
                    f"rise from low: {n(price_range.get('rise_from_low_percent'), '%')}; as of {price_range.get('as_of', 'Not verified')}.")
        setup = technical.get("setup_evaluation") or {}
        rows.append(f"Pattern/setup: {(setup.get('stage_1') or {}).get('category', 'Not verified')}; "
                    f"missing confirmation: {', '.join((setup.get('stage_2') or {}).get('missing', [])) or 'see entry evidence'}.")
        levels = item.get("levels") or {}
        rows.append(f"Support / resistance: ₹{n(levels.get('support'))} / ₹{n(levels.get('resistance'))}; reward/risk: {n(levels.get('risk_reward'), ':1')}.")
        friendly = {"valuation_quality": "PE range", "delivery_quality": "delivery versus monthly baseline",
                    "roe_quality": "ROE", "roce_quality": "ROCE", "quarterly_results_quality": "three-quarter growth",
                    "institutional_holding_quality": "FII/DII holdings", "promoter_holding_quality": "promoter holding",
                    "debt_free_quality": "zero reported borrowings"}
        passed = [label for key, label in friendly.items() if checks.get(key, {}).get("status") == "PASS"]
        rows.append("Measured strengths: " + (", ".join(passed) if passed else "no complete company-data requirement passed") + ".")
        pe = factor("valuation_quality")
        stock, sector = pe.get("stock_pe"), pe.get("sector_pe")
        deviation = ((stock / sector - 1) * 100 if stock is not None and sector and sector > 0 else None)
        if stock is not None:
            rows.append(f"PE: {n(stock)} versus sector {n(sector)}; deviation {n(deviation, '%')}. "
                        f"Allowed range: {n(pe.get('minimum_allowed_pe'))}–{n(pe.get('maximum_allowed_pe'))}. "
                        "Discounts greater than 5% also fail the configured matching rule.")
        for key, label in (("roe_quality", "ROE"), ("roce_quality", "ROCE")):
            data = factor(key)
            field = "roe_percent" if key == "roe_quality" else "roce_percent"
            rows.append(f"{label}: {n(data.get(field), '%')}; required at least {n(data.get('minimum'), '%')}.")
        debt = factor("debt_free_quality")
        rows.append(f"Reported borrowings: ₹{n(debt.get('total_debt'))} crore; "
                    f"debt/equity {n(debt.get('debt_to_equity'))}. The configured debt-free rule requires zero borrowings. "
                    "Reported amounts can include lease liabilities; lending-company borrowings are part of their funding business.")
        delivery = factor("delivery_quality")
        rows.append(f"Delivery: {n(delivery.get('delivery_percent'), '%')} versus "
                    f"{n(delivery.get('monthly_delivery_percent'), '%')} over the preceding 20 sessions. "
                    "The daily figure is from the latest completed session, not intraday delivery.")
        ownership = factor("institutional_holding_quality")
        for prefix in ("fii", "dii"):
            rows.append(f"{prefix.upper()}: {n(ownership.get(prefix+'_holding_percent'), '%')}; quarter-on-quarter change "
                        f"{n(ownership.get(prefix+'_holding_change_pct_points'))} percentage points. "
                        f"Minimum holding {n(ownership.get('minimum_'+prefix+'_holding_percent'), '%')}; changes must be non-negative.")
        promoter = factor("promoter_holding_quality")
        rows.append(f"Promoter: {n(promoter.get('promoter_holding_percent'), '%')}; change "
                    f"{n(promoter.get('promoter_holding_change_pct_points'))} percentage points; "
                    f"pledged/encumbered holding {n(promoter.get('promoter_pledge_percent'), '%')}. "
                    f"Required holding ≥{n(promoter.get('minimum_promoter_holding_percent'), '%')}, non-decreasing and unpledged.")
        quarter = factor("quarterly_results_quality")
        for key, label in (("revenue_growth_last_three_quarters", "Revenue"),
                           ("profit_growth_last_three_quarters", "Net profit")):
            values = quarter.get(key)
            text = ", ".join(n(value, "%") for value in values) if values else "Not verified on a comparable basis"
            rows.append(f"{label} YoY growth, latest three reported quarters (oldest to newest): {text}. "
                        "All three must be positive.")
        evidence = selection.get("data_evidence", {})
        commentary = evidence.get("commentary_strength", {})
        rows.append(f"Commentary check: {checks.get('commentary_quality', {}).get('status', 'UNKNOWN')}. "
                    f"Positive themes: {', '.join(commentary.get('positive_themes', [])) or 'not recorded'}; "
                    f"negative-signal count: {commentary.get('negative_signal_count', 'not recorded')}. "
                    "This is an interpretation of prepared remarks, not a company-reported rating.")
        for key, label in (("sector_one_year_quality", "One-year sector return"),
                           ("sector_leadership_quality", "Stock versus sector over one year"),
                           ("vwap_quality", "Current-session VWAP"), ("block_deal_quality", "Block-deal check")):
            check = checks.get(key, {})
            values = ", ".join(f"{name}: {n(value)}" for name, value in check.get("factors", {}).items()
                               if isinstance(value, (int, float)) and not isinstance(value, bool))
            rows.append(f"{label}: {check.get('status', 'UNKNOWN')}. {values} "
                        f"Reason: {', '.join(check.get('reason_codes', [])) or 'see evidence'}.")
        news = item.get("verified_news", [])
        if news:
            for event in news:
                rows.append(f"Verified news ({event['date']}, {event['classification']}): {event['summary']} "
                            f"[Source]({event['url']}).")
        else:
            rows.append("News: no independently verified stock-specific adverse development is recorded in this explanation. "
                        "A model sentiment flag alone does not establish an adverse event; unresolved items require review.")
        news_context = item.get("news") or {}
        rows.append(f"News check: {news_context.get('news_state', 'Not verified')}; model sentiment: {news_context.get('sentiment', 'Not verified')}; checked at {news_context.get('checked_at', 'Not verified')}.")
        for article in news_context.get("headlines", [])[:5]:
            rows.append(f"Latest collected headline ({article.get('published', 'date unavailable')}, {article.get('source', 'source unavailable')}): "
                        f"[{article.get('title', 'Headline')}]({article.get('url', '')}). A collected headline is not an independently verified catalyst.")
        return rows

    @classmethod
    def render(cls, report):
        items = report.get("reviewed", [])
        lines = [f"# Futures screening explanation — {(report.get('today_news') or {}).get('run_date', 'report snapshot')}",
                 "", f"Screened: {report.get('universe_size', 0)}. Reviewed: {len(items)}. "
                 f"Approved: {len(report.get('suggestions', []))}.", "",
                 "Company strengths and trade approval are separate. Each failed rule and unverified check is shown below.", ""]
        market_pe = report.get("market_pe") or {}
        cache = report.get("data_cache") or {}
        if cache.get("source") == "CACHE":
            lines.extend([f"Cached historical snapshot saved at {cache.get('saved_at')}; "
                          "market is outside the live window. Historical approvals are not current entry approvals.", ""])
        lines.extend([f"Market benchmark PE ({market_pe.get('benchmark', 'Nifty 50')}): {cls.number(market_pe.get('value'))}; "
                      f"as of {market_pe.get('as_of', 'Not verified')}.", ""])
        if items:
            technical = max(items, key=lambda item: item.get("technical_score", 0))
            lines.extend([f"Strongest technical score: **{technical['symbol']}** ({technical.get('technical_score')}/100).", ""])
            comparable = []
            for item in items:
                checks = (item.get("futures_selection") or {}).get("checks", {})
                roe = checks.get("roe_quality", {}).get("factors", {}).get("roe_percent")
                roce = checks.get("roce_quality", {}).get("factors", {}).get("roce_percent")
                if roe is not None and roce is not None:
                    comparable.append((roe + roce, item["symbol"], roe, roce))
            if comparable:
                _, symbol, roe, roce = max(comparable)
                lines.extend([f"Highest combined reported ROE/ROCE: **{symbol}** "
                              f"({cls.number(roe, '%')} / {cls.number(roce, '%')}).", ""])
            lines.extend(["| Stock | PE | Sector PE | Relative deviation | Borrowings ₹ crore |", "|---|---:|---:|---:|---:|"])
            for item in items:
                checks = (item.get("futures_selection") or {}).get("checks", {})
                pe = checks.get("valuation_quality", {}).get("factors", {})
                stock, sector = pe.get("stock_pe"), pe.get("sector_pe")
                deviation = ((stock / sector - 1) * 100 if stock is not None and sector else None)
                debt = checks.get("debt_free_quality", {}).get("factors", {}).get("total_debt")
                lines.append(f"| {item['symbol']} | {cls.number(stock)} | {cls.number(sector)} | "
                             f"{cls.number(deviation, '%')} | {cls.number(debt)} |")
            lines.append("")
        for item in items:
            lines.extend([f"## {item['symbol']} — {item.get('final_action', 'REVIEW')}", "",
                          f"Technical score: {item.get('technical_score', 'N/A')}/100.", ""])
            lines.extend("- " + reason for reason in cls.explain(item))
            lines.append("")
            sources = (item.get("futures_selection") or {}).get("data_evidence", {})
            lines.append("Sources and reporting periods:")
            for key, evidence in sources.items():
                if isinstance(evidence, dict) and evidence.get("url"):
                    lines.append(f"- {key}: [{evidence.get('period', 'report period')}]({evidence['url']}); {evidence.get('basis', '')}")
            lines.append("")
        return "\n".join(lines)
