"""Uncertainty is reported separately; original strategy points remain unchanged."""

def evidence_score(checks,names):
    states={name:'UNKNOWN' if value is None else 'PASS' if bool(value) else 'FAIL' for name,value in zip(names,checks)}
    passed=sum(v=='PASS' for v in states.values());unknown=sum(v=='UNKNOWN' for v in states.values())
    observed=len(states)-unknown
    return {'conditions':states,'confirmed_score':passed*10,'possible_score':(passed+unknown)*10,
            'coverage_percent':observed/len(states)*100,'observed_condition_score':passed/observed*100 if observed else None,
            'mode':'REPORT_ONLY_UNCALIBRATED'}


def stable_key(item):
    metrics=item.get('metrics') or {}
    category=item.get('classification')
    recovery=category=='RECOVERING_STOCKS'
    strength=(metrics.get('return_20d_percent') or 0)*(1 if recovery else -1)
    return (-(item.get('technical_score') or 0),-strength,item['symbol'])


def research_selection(evaluations,capacity,limit,existing=(),minimum_score=60):
    selected=set(existing);reviews=[{'symbol':e['symbol'],'reason':'CONFLICTING_VERIFIED_SECTOR_CLASSIFICATION'}
                                  for e in evaluations.values() if (e.get('sector_identity') or {}).get('status')=='REVIEW_REQUIRED']
    for category in ('SHORTING_STOCKS','RECOVERING_STOCKS'):
        group=sorted((e for e in evaluations.values() if e['classification']==category),key=stable_key)
        cutoff=group[min(capacity,len(group))-1]['technical_score'] if group else None
        eligible=[e for e in group if e['technical_score']>=cutoff] if cutoff is not None else []
        direction_selected={e['symbol'] for e in eligible[:limit]}
        selected.update(direction_selected)
        for e in eligible[limit:]:
            reviews.append({'symbol':e['symbol'],'direction':category,'reason':'BOUNDARY_TIE_EXCEEDS_CONTEXT_BUDGET'})
        for e in evaluations.values():
            assessment=(e.get('score_evidence') or {}).get('recovery' if category=='RECOVERING_STOCKS' else 'bearish') or {}
            if e['classification'] in (category,'NONE') and assessment.get('coverage_percent',100)<100 and assessment.get('possible_score',0)>=(cutoff if cutoff is not None else minimum_score):
                reviews.append({'symbol':e['symbol'],'direction':category,'reason':'MISSING_CONTEXT_MAY_CHANGE_RANK',
                                'confirmed_score':assessment.get('confirmed_score'),'possible_score':assessment.get('possible_score')})
                if len(direction_selected)<limit:
                    selected.add(e['symbol']);direction_selected.add(e['symbol'])
    return selected,reviews


def shadow_comparison(evaluations,capacity):
    result={'mode':'REPORT_ONLY','out_of_sample_validated':False,'groups':{}}
    for category in ('SHORTING_STOCKS','RECOVERING_STOCKS'):
        group=[e for e in evaluations.values() if e['classification']==category]
        baseline=sorted(group,key=stable_key)[:capacity]
        name='recovery' if category=='RECOVERING_STOCKS' else 'bearish'
        shadow=sorted(group,key=lambda e:(-((e.get('score_evidence',{}).get(name,{}) or {}).get('observed_condition_score') or e.get('technical_score',0)),
                    -((e.get('score_evidence',{}).get(name,{}) or {}).get('coverage_percent') or 0),*stable_key(e)))[:capacity]
        a={e['symbol'] for e in baseline};b={e['symbol'] for e in shadow}
        result['groups'][category]={'original_selection':[e['symbol'] for e in baseline],
            'coverage_shadow_selection':[e['symbol'] for e in shadow],'added_by_shadow':sorted(b-a),'removed_by_shadow':sorted(a-b)}
    return result
