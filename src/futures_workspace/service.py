"""Weekly full-master discovery and selected-only daily trading orchestration."""
from dataclasses import replace
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import math
import pandas as pd
from src.futures.sessions import ist, trading_day
from src.futures_workspace.config import WorkspaceConfig
from src.futures_workspace.store import WorkspaceStore, LISTS, timestamp
from src.futures_workspace.discovery import universe, technical, unknown, discovery_score
from src.futures_workspace.adapter import RepositoryAdapter
from src.futures_workspace.bias import MarketBias

class FuturesWorkspace:
    def __init__(self,platform,store=None,config=None,adapter=None,scanner_factory=None):
        self.platform=platform
        self.store=store or WorkspaceStore()
        self.config=config or replace(WorkspaceConfig.from_env(),**self.store.settings())
        self.adapter=adapter or RepositoryAdapter(platform,self.config)
        self.scanner_factory=scanner_factory

    def check_enabled(self):
        if not self.config.enabled:
            raise ValueError('Futures Trading is disabled; enable FUTURES_WORKSPACE_ENABLED')

    @staticmethod
    def now(value=None):
        return ist(value if value is not None else pd.Timestamp.now(tz='Asia/Kolkata'))

    @staticmethod
    def quote_quality(quote,config,now=None):
        try:
            price=float(quote['last_price']); volume=float(quote['volume'])
            buys=quote['depth']['buy']; sells=quote['depth']['sell']
            bid=float(buys[0]['price']); ask=float(sells[0]['price'])
            if not all(math.isfinite(v) for v in (price,volume,bid,ask)) or min(price,bid,ask)<=0 or ask<bid:
                raise ValueError('Invalid quote/depth')
            if now is not None:
                stamp=ist(quote.get('timestamp') or quote.get('last_trade_time'))
                if stamp>ist(now):
                    raise ValueError('Future quote timestamp')
                elapsed=sum(trading_day(d) for d in pd.date_range(stamp.normalize()+pd.Timedelta(days=1),ist(now).normalize(),freq='D'))
                if elapsed>config.maximum_history_age_sessions:
                    raise ValueError('Stale weekly Futures quote')
            spread=(ask-bid)/price*100
            return {'status':'PASS' if volume>=config.minimum_futures_volume and spread<=config.maximum_spread_percent else 'FAIL',
                'price':price,'volume':volume,'spread_percent':spread,'timestamp':str(quote.get('timestamp') or quote.get('last_trade_time') or 'UNKNOWN'),
                'basis':'LATEST_AVAILABLE_COMPLETED_SESSION_FOR_WEEKLY_DISCOVERY'}
        except (KeyError,TypeError,ValueError,IndexError):
            return {'status':'UNKNOWN','reason':'FUTURES_QUOTE_OR_LIQUIDITY_MISSING'}

    def refresh_universe(self,now=None):
        self.check_enabled(); now=self.now(now)
        with self.store.job('UNIVERSE_REFRESH') as (job_id,output):
            self.adapter.begin(now)
            try:
                contracts=universe(self.adapter.instruments(),now)
                if not contracts:
                    raise ValueError('No eligible stock Futures found; last state retained')
                output.update({'contracts':contracts,'eligible_universe_size':len(contracts),'as_of':now.isoformat()})
                self.store.record(job_id,'*','ELIGIBILITY',output)
            finally:
                self.adapter.end()
        return output

    def rotate(self,now=None,*,selected_only=False,key=None,progress=None):
        self.check_enabled(); now=self.now(now)
        if key:
            previous=next((j for j in self.store.jobs('WEEKLY_ROTATION') if j['job_key']==key and j['status']=='COMPLETED'),None)
            if previous:
                return previous['result']
        kind='RECHECK' if selected_only else 'WEEKLY_ROTATION'
        with self.store.job(kind,key) as (job_id,output):
            self.adapter.begin(now)
            try:
                contracts=universe(self.adapter.instruments(),now)
                if not contracts:
                    raise ValueError('Instrument universe unavailable; prior lists preserved')
                existing={m['symbol']:m for m in self.store.members()}
                symbols=sorted(existing) if selected_only else sorted(contracts)
                try:
                    quotes=self.adapter.quotes({s:contracts[s] for s in symbols if s in contracts})
                except Exception:
                    quotes={}
                try:
                    benchmark=self.adapter.history('NIFTY 50')
                except Exception:
                    benchmark=None
                evaluations={}
                for i,symbol in enumerate(symbols):
                    contract=contracts.get(symbol)
                    if contract is None:
                        item=unknown('ACTIVE_CONTRACT_MISSING_REVIEW_REQUIRED')
                    else:
                        try:
                            item=deepcopy(technical(self.adapter.history(symbol),self.config,now,benchmark,self.adapter.sector_history(symbol)))
                        except Exception as exc:
                            item=unknown('HISTORY_FETCH_FAILED:'+type(exc).__name__)
                    q=self.quote_quality(quotes.get('NFO:'+contract['tradingsymbol'],{}),self.config,now) if contract else {'status':'UNKNOWN'}
                    item.update({'symbol':symbol,'contract':contract,'futures_quality':q,'evaluated_at':now.isoformat(),
                                 'sector':self.adapter.sector(symbol)})
                    try:
                        fundamental=self.adapter.fundamentals(symbol,now)
                    except Exception as exc:
                        fundamental={'status':'UNKNOWN','bullish_score':None,'bearish_score':None,'reason':type(exc).__name__}
                    try:
                        news=self.adapter.news(symbol,now)
                    except Exception as exc:
                        news={'status':'UNKNOWN','bullish_score':None,'bearish_score':None,'reason':type(exc).__name__}
                    item['fundamentals']=fundamental; item['news_events']=news
                    event=news.get('events') or {}
                    if event.get('hard_block') and event.get('event_data_availability_state')=='COMPLETE':
                        item['classification']='TRANSITION'
                        item['reason_codes'].append('VERIFIED_MAJOR_EVENT_RISK_REVIEW_REQUIRED')
                    item['discovery']=discovery_score(item,fundamental,news,self.config)
                    item['discovery_mode']='TECHNICAL_ONLY' if fundamental.get('status')=='UNKNOWN' else 'TECHNICAL_WITH_CONTEXT'
                    if q['status']!='PASS' and item['classification'] not in ('UNKNOWN_DATA','TRANSITION'):
                        item['classification']='UNKNOWN_DATA'
                        item['reason_codes'].append('FUTURES_LIQUIDITY_UNVERIFIED')
                    evaluations[symbol]=item
                    for evidence_kind,payload in (('DISCOVERY',item),('FUNDAMENTAL',fundamental),('NEWS_EVENT',news)):
                        self.store.record(job_id,symbol,evidence_kind,payload)
                    if progress:
                        progress(i+1,len(symbols),symbol)
                if not any(e['classification'] not in ('UNKNOWN_DATA','TRANSITION') or 'VERIFIED_MAJOR_EVENT_RISK_REVIEW_REQUIRED' in e.get('reason_codes',[]) for e in evaluations.values()):
                    # Persist an incomplete attempt but never publish a destructive empty version.
                    output.update({'status':'INCOMPLETE','eligible_universe_size':len(contracts),'evaluated_count':len(evaluations),
                        'evaluations':evaluations,'changes':[],'as_of':now.isoformat(),'reason':'NO_RELIABLE_CLASSIFICATIONS_LAST_VERSION_PRESERVED'})
                else:
                    members,changes=self._rotation(existing,evaluations,now,selected_only)
                    output.update({'status':'COMPLETE','eligible_universe_size':len(contracts),'evaluated_count':len(evaluations),
                        'evaluations':evaluations,'changes':changes,'as_of':now.isoformat(),'data_sources':['KITE','EXISTING_PUBLIC_FUNDAMENTALS','EXISTING_NEWS_EVENTS'],
                        'data_quality_failures':[s for s,e in evaluations.items() if e['classification']=='UNKNOWN_DATA']})
                    with self.store.transaction() as c:
                        output['version']=self.store.publish(c,members,kind,output)
                self.store.record(job_id,'*','ELIGIBILITY',{'contracts':contracts,'as_of':now.isoformat()})
            finally:
                self.adapter.end()
        return output

    def _rotation(self,existing,evaluations,now,selected_only):
        next_members={}; changes=[]
        for symbol,old in existing.items():
            item=evaluations.get(symbol)
            if item is None or item['classification'] in ('UNKNOWN_DATA','TRANSITION'):
                next_members[symbol]={**old,'status':'REVIEW_REQUIRED','evaluated_at':now.isoformat(),
                    'evidence':{**old['evidence'],'latest_review':item or {'reason':'MISSING_FROM_MASTER'}}}
                changes.append({'symbol':symbol,'action':'REVIEW_REQUIRED','from':old['category'],'to':old['category']})
                continue
            category=item['classification']
            if old['pinned']:
                next_members[symbol]={**old,'status':'ACTIVE' if category==old['category'] else 'REVIEW_REQUIRED',
                    'evaluated_at':now.isoformat(),'evidence':item}
                changes.append({'symbol':symbol,'action':'PINNED_RETAINED','from':old['category'],'to':old['category']})
                continue
            old_score=(old['evidence'].get('discovery') or {}).get('ranking_score') or self.config.minimum_score
            score=item['discovery']['ranking_score'] or 0
            if category==old['category'] or (category=='NONE' and score>=self.config.minimum_score-self.config.hysteresis):
                next_members[symbol]={**old,'status':'ACTIVE' if category==old['category'] else 'REVIEW_REQUIRED','evaluated_at':now.isoformat(),'evidence':item}
                changes.append({'symbol':symbol,'action':'RETAINED','from':old['category'],'to':old['category']})
            elif category in LISTS and score<max(self.config.minimum_score+self.config.hysteresis,old_score):
                next_members[symbol]={**old,'status':'REVIEW_REQUIRED','evaluated_at':now.isoformat(),'evidence':{**old['evidence'],'latest_review':item}}
                changes.append({'symbol':symbol,'action':'HYSTERESIS_RETAINED','from':old['category'],'to':old['category']})
            # Strong confirmed transfer or removal is handled by the ranked admission below.
        for category in LISTS:
            candidates=sorted((e for e in evaluations.values() if e['classification']==category),key=lambda e:e['discovery']['ranking_score'] or 0,reverse=True)
            if selected_only:
                candidates=[e for e in candidates if e['symbol'] in existing]
            pinned=sum(m['category']==category and m['pinned'] for m in next_members.values())
            slots=max(0,self.config.maximum_per_list-pinned)
            qualifying=[]
            for e in candidates:
                symbol=e['symbol']; old=existing.get(symbol)
                retained=next_members.get(symbol)
                if retained and (retained['pinned'] or retained['category']!=category):
                    continue
                if old and old['category']!=category and e['discovery']['ranking_score']<max(self.config.minimum_score+self.config.hysteresis,(old['evidence'].get('discovery') or {}).get('ranking_score') or self.config.minimum_score):
                    continue
                qualifying.append(e)
            # Reliable retained members compete by score; REVIEW_REQUIRED members never vanish for missing data.
            for e in qualifying[:slots]:
                symbol=e['symbol']; old=existing.get(symbol)
                next_members[symbol]={'symbol':symbol,'category':category,'enabled':old['enabled'] if old else True,
                    'pinned':old['pinned'] if old else False,'status':'ACTIVE','added_at':old['added_at'] if old and old['category']==category else now.isoformat(),
                    'evaluated_at':now.isoformat(),'evidence':e}
            selected={e['symbol'] for e in qualifying[:slots]}
            for symbol in list(next_members):
                m=next_members[symbol]
                if m['category']==category and not m['pinned'] and m['status']=='ACTIVE' and symbol not in selected:
                    del next_members[symbol]
        # One final audit row per stock; explain final previous/current membership.
        changes=[]
        for symbol in sorted(set(existing)|set(next_members)):
            old=existing.get(symbol); new=next_members.get(symbol)
            action='ADDED' if old is None else 'REMOVED' if new is None else 'TRANSFERRED' if old['category']!=new['category'] else 'REVIEW_REQUIRED' if new['status']!='ACTIVE' else 'PINNED_RETAINED' if new['pinned'] else 'UNCHANGED'
            changes.append({'symbol':symbol,'action':action,'from':old['category'] if old else None,'to':new['category'] if new else None,
                'reason_codes':evaluations.get(symbol,{}).get('reason_codes',['MASTER_SECURITY_MISSING']),
                'confidence':evaluations.get(symbol,{}).get('confidence',0)})
        return list(next_members.values()),changes

    def scan_daily(self,now=None):
        self.check_enabled(); fixed_now=now; now=self.now(now)
        with self.store.job('DAILY_TRADING') as (job_id,output):
            members=self.store.members(active=True)
            symbols=sorted({m['symbol'] for m in members})
            if not symbols:
                output.update({'status':'EMPTY_WATCHLISTS','generated_at':now.isoformat(),'long':[],'short':[],
                    'other':[],'selected_count':0,'reason':'Initialize using Weekly Rotation; daily scan never discovers stocks'})
                return output
            membership={m['symbol']:m for m in members}
            from src.futures.config import FuturesScanConfig
            from src.futures.scanner import FuturesOpportunityScanner
            legacy=FuturesScanConfig.from_env()
            # Never relax an earlier existing deadline or any existing risk gate.
            entry=min((legacy.entry_cutoff_hour,legacy.entry_cutoff_minute),(self.config.entry_cutoff_hour,self.config.entry_cutoff_minute))
            flat=min((legacy.intraday_exit_hour,legacy.intraday_exit_minute),(self.config.flat_hour,self.config.flat_minute))
            config=replace(legacy,entry_cutoff_hour=entry[0],entry_cutoff_minute=entry[1],intraday_exit_hour=flat[0],intraday_exit_minute=flat[1],
                           review_per_direction=max(legacy.review_per_direction,len(symbols)))
            factory=self.scanner_factory or FuturesOpportunityScanner
            scanner=factory(self.platform,config=config)
            if self.scanner_factory is None:
                from src.futures_workspace.adapter import SelectedScanProvider
                scanner.provider=SelectedScanProvider(self.platform.provider,symbols)
            # Explicit selected_symbols prevents _universe_symbols and all preparation discovery.
            report=scanner.scan(limit=50,selected_symbols=symbols,now=fixed_now,include_backtest=False)
            report=self.platform._serialize(report)
            try:
                bias=self.adapter.market_bias(now if fixed_now is not None else self.now())
            except Exception:
                bias=MarketBias.evaluate({})
            buckets={'long':[],'short':[],'other':[]}
            reviewed=report.get('reviewed',[])
            by_symbol={s:[] for s in symbols}
            for r in reviewed:
                if r.get('symbol') in by_symbol:
                    by_symbol[r['symbol']].append(r)
            for symbol in symbols:
                member=membership[symbol]
                desired='SHORT' if member['category']==LISTS[0] else 'LONG'
                rows=by_symbol[symbol]
                contradictory=any(r.get('side')!=desired and r.get('final_decision')=='APPROVED' for r in rows)
                item=next((dict(r) for r in rows if r.get('side')==desired),{'symbol':symbol,'side':desired,'final_decision':'UNKNOWN','reason_codes':['SCANNER_RESULT_MISSING']})
                item.update({'watchlist':member['category'],'weekly_discovery_score':member['evidence'].get('discovery',{}).get('ranking_score'),
                    'original_daily_score':item.get('technical_score'),'market_bias':bias,
                    'bias_adjustment_report_only':bias['short_adjustment' if desired=='SHORT' else 'long_adjustment'],
                    'counter_trend':bias['long_adjustment']>0 if desired=='SHORT' else bias['long_adjustment']<0,
                    'weekly_confidence':member['evidence'].get('confidence'),
                    'workspace_flat_deadline_ist':f'{flat[0]:02}:{flat[1]:02}', 'manual_execution_only':True})
                age=(now-ist(member['evaluated_at'])).total_seconds()/86400
                if not 0<=age<=self.config.maximum_membership_age_days:
                    item['final_decision']='UNKNOWN'
                    item['reason_codes']=[*item.get('reason_codes',[]),'WEEKLY_MEMBERSHIP_STALE_RECHECK_REQUIRED']
                item['workspace_decision']='CONFLICT' if contradictory else 'ELIGIBLE' if item.get('final_decision')=='APPROVED' else 'UNKNOWN_DATA' if item.get('final_decision')=='UNKNOWN' else 'NO_TRADE'
                if contradictory:
                    item['reason_codes']=[*item.get('reason_codes',[]),'OPPOSITE_DIRECTION_APPROVED_REVIEW_REQUIRED']
                buckets['short' if desired=='SHORT' else 'long'].append(item) if item['workspace_decision']=='ELIGIBLE' else buckets['other'].append(item)
            for name in ('long','short'):
                buckets[name].sort(key=lambda r:r.get('original_daily_score') or 0,reverse=True)
            output.update({'status':'COMPLETED','selected_count':len(symbols),'selected_symbols':symbols,
                'generated_at':report.get('generated_at',self.now().isoformat()),'market_bias':bias,**buckets,
                'report':report,'watchlist_version':self.store.versions()[0]['id'] if self.store.versions() else None,
                'policy':'Recommendations only. Close positions manually by 15:10 IST. No broker orders.'})
            self.store.record(job_id,'*','MARKET_BIAS',bias)
        return output

    def manage(self,symbol,action,category=None):
        self.check_enabled()
        evidence=None
        if action=='add':
            now=self.now(); self.adapter.begin(now)
            try:
                contracts=universe(self.adapter.instruments(),now)
                symbol=symbol.strip().upper()
                if symbol not in contracts:
                    raise ValueError('Symbol is not an eligible NSE stock Futures security')
                evidence={'contract':contracts[symbol],'reason_codes':['MANUAL_ADD_REQUIRES_RECHECK'],'confidence':0}
            finally:
                self.adapter.end()
        return self.store.manage(symbol,action,category,evidence)

    def performance(self):
        rotations=[v for v in self.store.versions() if v['kind']=='WEEKLY_ROTATION']
        runs=[j for j in self.store.jobs('DAILY_TRADING') if j['status']=='COMPLETED']
        signals=[r for j in runs for k in ('long','short') for r in (j['result'] or {}).get(k,[])]
        changes=[c for v in rotations for c in v['report'].get('changes',[])]
        return {'daily_scan_count':len(runs),'eligible_signal_count':len(signals),'rotation_count':len(rotations),
            'membership_change_count':sum(c['action'] in ('ADDED','REMOVED','TRANSFERRED') for c in changes),
            'turnover_by_version':[{'version':v['id'],'turnover':sum(c['action'] in ('ADDED','REMOVED','TRANSFERRED') for c in v['report'].get('changes',[]))/max(1,len(v['snapshot']))} for v in rotations],
            'by_direction':{d:sum(r['side']==d for r in signals) for d in ('LONG','SHORT')},
            'by_sector':{s:sum(r.get('sector')==s for r in signals) for s in sorted({str(r.get('sector','UNKNOWN')) for r in signals})},
            'win_rate':None,'net_expectancy':None,'profit_factor':None,'maximum_drawdown':None,
            'validation_status':'UNKNOWN_NO_LINKED_MANUAL_FILLS_OR_POINT_IN_TIME_UNIVERSE',
            'ranking_ablation':'NOT_VALIDATED_CONTEXT_REPORT_ONLY'}

    def replay(self,payload):
        self.check_enabled()
        from src.futures_workspace.performance import WorkspaceResearchReplay
        with self.store.job('RESEARCH_REPLAY') as (job_id,output):
            output.update(WorkspaceResearchReplay(self.config).run(payload))
            self.store.record(job_id,'*','PERFORMANCE',output)
        return output
