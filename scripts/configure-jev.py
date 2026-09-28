#!/usr/bin/env python3
"""Run in your terminal. The API key is never echoed or placed in shell history."""
import getpass
import json
import os
from pathlib import Path
os.umask(0o077)
directory = Path(__file__).resolve().parent.parent / '.local'
directory.mkdir(exist_ok=True, mode=0o700)
key = getpass.getpass('Jev / TypeSafe API key (hidden): ').strip()
if not key: raise SystemExit('Nothing saved.')
path = directory / 'jev.json'
tmp = directory / 'jev.json.tmp'
with tmp.open('w') as f: json.dump({'api_key': key, 'model': 'jev-1.13.0'}, f)
os.chmod(tmp, 0o600)
tmp.replace(path)
print('Saved locally. You can return to Mail.')
