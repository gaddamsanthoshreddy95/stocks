"""Use the CLI daily workflow in a fresh process, sharing only persisted state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]


def run_daily(database,market_data_source='kite'):
    environment=os.environ.copy()
    saved=dotenv_values(ROOT/'.env',interpolate=False)
    pair={name:(saved.get(name) or '').strip() for name in ('KITE_API_KEY','KITE_ACCESS_TOKEN')}
    if all(pair.values()):environment.update(pair)
    environment['MARKET_DATA_SOURCE']=market_data_source
    with tempfile.TemporaryDirectory(prefix='futures-daily-') as directory:
        result=Path(directory)/'result.json'
        command=[sys.executable,str(ROOT/'scripts/run_futures_workspace.py'),'daily',
                 '--database',str(Path(database).resolve()),'--result-file',str(result)]
        # Keep the child's traceback in the Streamlit terminal. Never use a shell.
        completed=subprocess.run(command,cwd=ROOT,env=environment,check=False)
        if completed.returncode or not result.exists():
            raise RuntimeError('The isolated daily scan failed. See the Streamlit terminal for its traceback; previous saved results remain historical.')
        return json.loads(result.read_text(encoding='utf-8'))
