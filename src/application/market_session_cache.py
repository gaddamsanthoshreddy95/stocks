"""Refresh command results at any time and persist complete snapshots."""
from copy import copy
from dataclasses import asdict
from datetime import datetime, time
from functools import wraps
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from src.application.errors import ValidationError

LOGGER = logging.getLogger(__name__)
CACHE_VERSION = 1


def session_now():
    return datetime.now(ZoneInfo('Asia/Kolkata'))


def live_session(now=None):
    now = now or session_now()
    now = now.replace(tzinfo=ZoneInfo('Asia/Kolkata')) if now.tzinfo is None else now.astimezone(ZoneInfo('Asia/Kolkata'))
    try:
        end = time.fromisoformat(os.getenv('MARKET_LIVE_END_IST', '15:30'))
    except ValueError as exc:
        raise ValidationError('MARKET_LIVE_END_IST must be HH:MM') from exc
    if end not in {time(15,15),time(15,30)}:
        raise ValidationError('MARKET_LIVE_END_IST must be 15:15 or 15:30')
    # Optional local holiday list; this policy needs no calendar network lookup.
    holidays = {v.strip() for v in os.getenv('MARKET_HOLIDAYS_IST', '').split(',') if v.strip()}
    return now.weekday() < 5 and now.date().isoformat() not in holidays and time(9,15) <= now.time().replace(tzinfo=None) < end


def cached_command(name):
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            if self.settings.market_data_source != 'kite':
                return function(self, *args, **kwargs)
            # Nested commands share the refresh already in progress.
            if getattr(self, '_market_live_run', False):
                return function(self, *args, **kwargs)
            now = session_now()
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            parameters = {k:v for k,v in bound.arguments.items() if k != 'self'}
            if parameters.get('limit', 1) is None:
                parameters['limit'] = self.settings.final_report_limit
            if parameters.get('minimum_score', 1) is None:
                parameters['minimum_score'] = self.settings.minimum_technical_score
            if 'symbol' in parameters:
                parameters['symbol'] = self._symbol(parameters['symbol'])
            identity = json.dumps({'version':CACHE_VERSION,'command':name,
                                   'parameters':parameters,'settings':asdict(self.settings),
                                   'futures_thresholds':{k:v for k,v in os.environ.items() if k.startswith('FUTURES_')}},
                                  sort_keys=True, default=lambda value: sorted(value) if isinstance(value,set) else repr(value))
            key = hashlib.sha256(identity.encode()).hexdigest()
            root = Path(os.getenv('MARKET_REPORT_CACHE_DIR', '.cache/market_reports'))
            path = root / f'{name}_{key}.json'
            runner = copy(self)
            runner._market_live_run = True
            refresh = False
            try:
                if name in {'analyze', 'backtest'}:
                    with runner._analysis_cache_lock:
                        runner._analysis_cache.pop(parameters['symbol'], None)
                    begin = getattr(runner.provider, 'begin_live_refresh', None)
                    if begin:
                        begin([parameters['symbol']])
                        refresh = True
                result = function(runner,*args,**kwargs)
            finally:
                if refresh:
                    runner.provider.end_live_refresh()
            result['data_cache'] = {'source':'LIVE','saved_at':now.isoformat(),
                                   'historical_snapshot':False}
            payload = {'version':CACHE_VERSION,'key':key,'saved_at':now.isoformat(),
                       'result':self._serialize(result)}
            temporary = None
            try:
                root.mkdir(parents=True,exist_ok=True)
                with NamedTemporaryFile(mode='w',dir=root,suffix='.tmp',delete=False) as stream:
                    temporary = Path(stream.name)
                    # CLI suggestions already expose DataFrames as strings; persist
                    # full public reports without requiring pickle deserialization.
                    json.dump(payload,stream,default=str)
                os.replace(temporary,path)
            except (OSError,ValueError,TypeError) as exc:
                LOGGER.warning('Could not persist %s snapshot: %s',name,type(exc).__name__)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return result
        return wrapped
    return decorate
