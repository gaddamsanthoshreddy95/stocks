"""Offline coverage and paired entry-quality replay. No scans or broker calls."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reports',default='reports/futures*.json',help='Saved report glob for read-only coverage audit')
    parser.add_argument('--bundle',help='Optional JSON with recorded samples and histories keyed by exact contract')
    parser.add_argument('--output',default='reports/entry_quality_validation.json')
    args=parser.parse_args()
    from src.futures.entry_quality_backtest import audit_saved_reports,compare
    from glob import glob
    target=Path(args.output).resolve()
    protected={Path(p).resolve() for p in glob(args.reports)}
    if args.bundle:protected.add(Path(args.bundle).resolve())
    if target in protected:parser.error('Output must not overwrite a historical report or replay input')
    if args.bundle:
        import pandas as pd
        bundle=json.loads(Path(args.bundle).read_text(encoding='utf-8'))
        histories={}
        for contract,rows in bundle.get('histories',{}).items():
            frame=pd.DataFrame(rows);frame.index=pd.to_datetime(frame.pop('timestamp'),utc=True)
            histories[contract]=frame
        result=compare(bundle['samples'],histories)
    else:result=audit_saved_reports(glob(args.reports))
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'status':result['status'],'output':str(target),
        'saved_runs':result.get('saved_runs'),'recorded_input_snapshots':result.get('recorded_input_snapshots')}))


if __name__=='__main__':main()
