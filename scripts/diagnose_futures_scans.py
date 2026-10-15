"""Read saved scanner failures without starting scans or accessing Kite."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3


def summarize(report):
    rows=report.get('reviewed')
    if rows is None:
        rows=[item for bucket in ('long','short','other') for item in report.get(bucket,[])]
    return {'generated_at':report.get('generated_at'),'scan_mode':report.get('scan_mode'),
        'status':report.get('status'),'rows':len(rows),
        'scored_rows':sum(i.get('original_daily_score',i.get('technical_score')) is not None for i in rows),
        'reasons':dict(Counter(r for i in rows for r in i.get('reason_codes',[]))),
        'score_diagnostics':report.get('score_diagnostics'),
        'failures':report.get('failures',[]),'source_failures':report.get('source_failures',{}),
        'examples':[{'symbol':i.get('symbol'),'side':i.get('side'),
            'score':i.get('original_daily_score',i.get('technical_score')),
            'reasons':i.get('reason_codes',[]),'score_failure':i.get('score_failure'),
            'intraday_score_failure':i.get('intraday_score_failure')} for i in rows[:3]]}


def diagnose(path):
    result={}
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as connection:
        tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'ft_jobs' in tables:
            row=connection.execute("SELECT started_at,status,result,error FROM ft_jobs WHERE kind='DAILY_TRADING' ORDER BY started_at DESC LIMIT 1").fetchone()
            if row:result['Futures Trading']={'job_started':row[0],'job_status':row[1],'job_error':row[3],**summarize(json.loads(row[2] or '{}'))}
        if 'futures_scan_runs' in tables:
            row=connection.execute('SELECT report_json FROM futures_scan_runs ORDER BY id DESC LIMIT 1').fetchone()
            if row:result['Futures LONG / SHORT']=summarize(json.loads(row[0]))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',default=str(Path(__file__).resolve().parents[1]/'data/ui/stock_analyzer.db'))
    print(json.dumps(diagnose(parser.parse_args().database),indent=2,default=str))
