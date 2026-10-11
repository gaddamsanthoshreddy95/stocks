"""Start the local UI with the broker credentials saved by access.py."""
import os
from pathlib import Path
import runpy
import sys
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def load_local_kite_credentials(env_path):
    values = dotenv_values(env_path, interpolate=False)
    names = ('KITE_API_KEY', 'KITE_ACCESS_TOKEN')
    pair = {name: (values.get(name) or '').strip() for name in names}
    # Never combine half a file pair with an inherited credential.
    if all(pair.values()):
        os.environ.update(pair)


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    load_local_kite_credentials(ROOT / '.env')
    sys.argv = ['streamlit', 'run', str(ROOT / 'ui_app.py'), *sys.argv[1:]]
    runpy.run_module('streamlit', run_name='__main__')


if __name__ == '__main__':
    main()
