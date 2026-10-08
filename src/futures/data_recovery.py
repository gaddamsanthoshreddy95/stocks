"""Bridge existing provider caches into preparation without replacing either pipeline."""
from dataclasses import asdict
from pathlib import Path
import json
import re
import os
import pandas as pd
from src.data_provider.kite_data_provider import KiteDataProvider
from src.quality.models import FundamentalSnapshot
from src.sector.sector_strength import SectorStrength

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_FIELDS = ('pe_ratio', 'sector_pe', 'delivery_percent', 'monthly_delivery_percent')


def completed_session_cutoff(now):
    day = now.normalize()
    if now < day + pd.Timedelta(hours=15, minutes=30):
        day -= pd.Timedelta(days=1)
    holidays = {v.strip() for v in os.getenv('MARKET_HOLIDAYS_IST','').split(',')}
    while day.weekday() >= 5 or day.date().isoformat() in holidays:
        day -= pd.Timedelta(days=1)
    return day + pd.Timedelta(hours=15,minutes=30)


def source_failure(exc):
    """Keep useful source diagnostics while redacting credentials in transport URLs."""
    detail = str(exc)
    detail = re.sub(r'(?i)(api_key|access_token|api_secret|authorization)([= :]+)[^&\s\"\']+', r'\1\2[REDACTED]', detail)
    return {'type': type(exc).__name__, 'detail': detail[:1200],
            'category': 'DNS_RESOLUTION_FAILED' if any(s in detail for s in ('NameResolutionError', 'Failed to resolve', 'Name or service not known')) else 'SOURCE_REQUEST_FAILED'}


def completed_frame(frame, now, interval):
    if frame is None or frame.empty:
        return None
    from src.futures.sessions import normalise_candles
    frame = normalise_candles(frame, interval, now)
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    frame.index = index.tz_localize('Asia/Kolkata') if index.tz is None else index.tz_convert('Asia/Kolkata')
    frame = frame.loc[~frame.index.duplicated(keep='last')].sort_index()
    cutoff = now.normalize() + pd.Timedelta(hours=15, minutes=30)
    if interval == 'day':
        frame = frame.loc[frame.index.normalize() <= now.normalize()] if now >= cutoff else frame.loc[frame.index.date < now.date()]
    else:
        frame = frame.loc[frame.index + pd.Timedelta(minutes=5) <= min(now, cutoff)]
    # Never let a legacy live snapshot enter completed-session research.
    frame = frame.drop(columns=['IS_LIVE_CANDLE', 'LIVE_SESSION_PROGRESS'], errors='ignore')
    return frame if not frame.empty else None


def record_company(manager, payload, stamp, source):
    facts = FundamentalSnapshot(**payload)
    if facts.symbol not in manager.symbols or not facts.as_of:
        return False
    as_of = pd.Timestamp(facts.as_of, tz='Asia/Kolkata')
    if as_of.date() > manager.now.date() or (manager.now-as_of).total_seconds() >= manager.runtime.research_ttl_seconds:
        return False
    if stamp > manager.now or (manager.now-stamp).total_seconds() >= manager.runtime.research_ttl_seconds:
        return False
    manager.cache.put('company', facts.symbol, asdict(facts), now=stamp, ttl=manager.runtime.research_ttl_seconds)
    from src.futures.preparation import next_session
    market_expiry = pd.Timestamp(next_session(as_of.normalize()), tz='Asia/Kolkata') + pd.Timedelta(hours=16)
    if market_expiry > manager.now:
        manager.cache.put('market_fields', facts.symbol, {'values': {k:getattr(facts,k) for k in MARKET_FIELDS},
                          'evidence': {k:v for k,v in facts.evidence.items() if k in MARKET_FIELDS}},
                          now=stamp, ttl=(market_expiry-stamp).total_seconds())
    manager.cache.put('provenance', 'company:'+facts.symbol, {'source':source,'original_saved_at':stamp.isoformat()},
                      now=stamp, ttl=manager.runtime.research_ttl_seconds)
    return True


def recover_existing_data(manager):
    """Import only missing records; never use scores/plans from an old report as new signals."""
    original = manager.scanner.provider
    result = {'candles_imported':0, 'companies_imported':0, 'news_imported':0}
    if not isinstance(original, KiteDataProvider):
        return result
    names = sorted(set(manager.symbols)|set(SectorStrength.KITE_INDEX_SYMBOLS.values())|{'NIFTY 50'})
    for symbol in names:
        paths = [('day', original._history_path(symbol)), ('annual', original._long_history_path(symbol, '2y')),
                 ('5minute', original._intraday_cache_directory / f'{symbol}_45d_5minute.parquet')]
        for interval, path in paths:
            identity = f'NSE:{symbol}:{interval}'
            if manager.cache.has_frame(identity) or not path.is_file():
                continue
            try:
                frame = completed_frame(pd.read_parquet(path), manager.now, 'day' if interval == 'annual' else interval)
                if frame is None:
                    continue
                # Frame expiry follows the data timestamp, never the migration time.
                stamp = frame.index[-1] + (pd.Timedelta(hours=15, minutes=30) if interval in ('day','annual') else pd.Timedelta(minutes=5))
                if stamp > manager.now or (manager.now-stamp).total_seconds() >= manager.runtime.history_ttl_seconds:
                    continue
                manager.cache.put_frame(identity, frame, now=stamp, ttl=manager.runtime.history_ttl_seconds)
                manager.cache.put('provenance', identity, {'source':str(path.resolve()),'last_candle_at':frame.index[-1].isoformat()}, now=stamp, ttl=manager.runtime.history_ttl_seconds)
                result['candles_imported'] += 1
            except (OSError, ValueError, KeyError, TypeError) as exc:
                manager.errors['legacy:'+identity] = source_failure(exc)
    for contract in manager.contracts.values():
        for interval in ('day','5minute'):
            identity = 'NFO:'+contract['tradingsymbol']+':'+interval
            path = original._intraday_cache_directory / f"{contract['tradingsymbol']}_futures_{interval}.parquet"
            if manager.cache.has_frame(identity) or not path.is_file():
                continue
            try:
                frame = completed_frame(pd.read_parquet(path), manager.now, interval)
                if frame is not None:
                    stamp = frame.index[-1] + (pd.Timedelta(hours=15,minutes=30) if interval == 'day' else pd.Timedelta(minutes=5))
                    if stamp <= manager.now:
                        manager.cache.put_frame(identity, frame, now=stamp, ttl=manager.runtime.history_ttl_seconds)
                        result['candles_imported'] += 1
            except (OSError, ValueError, TypeError) as exc:
                manager.errors['legacy:'+identity] = source_failure(exc)
    missing = {s for s in manager.symbols if manager.cache.get('company',s,now=manager.now) is None}
    provider = manager.scanner.company_research.engine.fundamental_provider
    for symbol in list(missing):
        reader = getattr(provider, 'read_cached_snapshot', None)
        cached = reader(symbol, max_age_seconds=manager.runtime.research_ttl_seconds) if reader else None
        if cached and record_company(manager,cached['payload'],pd.Timestamp(cached['saved_at']),cached['source']):
            result['companies_imported'] += 1
            missing.discard(symbol)
    # Recover dated raw facts retained by earlier reference-mode executions. Do not
    # replay their scores, company PASS/FAIL outcomes, trade plans or quotes.
    if missing:
        folder = Path(manager.runtime.legacy_reports_directory)
        if not folder.is_absolute():
            folder = PROJECT_ROOT / folder
        paths = sorted(folder.glob('futures*.json'), key=lambda p:p.stat().st_mtime, reverse=True)
        for path in paths:
            if not missing:
                break
            try:
                if path.stat().st_size == 0:
                    continue  # A report currently being written is not a cache failure.
                report = json.loads(path.read_text())
                if not isinstance(report.get('reviewed'),list) or not report.get('generated_at'):
                    continue  # Benchmark/config JSON files are not research records.
                stamp = pd.Timestamp(report['generated_at'])
                if stamp.tz is None or stamp > manager.now or (manager.now-stamp).total_seconds() >= manager.runtime.research_ttl_seconds:
                    continue
                for item in report.get('reviewed', []):
                    symbol = item.get('symbol')
                    facts = (item.get('company_research') or {}).get('fundamentals')
                    if symbol not in missing or not facts:
                        continue
                    if record_company(manager, facts, stamp, str(path.resolve())):
                        result['companies_imported'] += 1
                        missing.discard(symbol)
                        news = item.get('news') or {}
                        checked = pd.Timestamp(news.get('checked_at')) if news.get('checked_at') else None
                        if checked is not None and checked.tz is not None and checked <= manager.now and manager.cache.get('news',symbol,now=manager.now,allow_expired=True) is None:
                            manager.cache.put('news',symbol,news,now=checked,ttl=manager.runtime.news_ttl_seconds)
                            result['news_imported'] += 1
            except (OSError, ValueError, TypeError, KeyError) as exc:
                manager.errors['legacy_report:'+str(path)] = source_failure(exc)
    return result
