import json
import os
from pathlib import Path

root = Path(SPECPATH)
datas = [(str(root / 'src' / 'assets'), 'assets'),
         (str(root / 'resources'), 'resources')]
public_oauth_client = os.getenv('XCAL_PUBLIC_OAUTH_CLIENT_FILE', '').strip()
if public_oauth_client:
    client_path = Path(public_oauth_client).expanduser().resolve()
    payload = json.loads(client_path.read_text(encoding='utf-8-sig'))
    installed = payload.get('installed') if isinstance(payload, dict) else None
    if not isinstance(installed, dict) or not installed.get('client_id'):
        raise ValueError('XCAL_PUBLIC_OAUTH_CLIENT_FILE must be a Google Desktop OAuth client JSON file.')
    datas.append((str(client_path), 'resources'))
analysis = Analysis(
    [str(root / 'src' / 'main.py')],
    pathex=[str(root / 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    excludes=['tkinter'],
    noarchive=False,
)
archive = PYZ(analysis.pure)
# Google downloads use client_secret_<id>.json; runtime lookup uses one stable name.
if public_oauth_client:
    analysis.datas = [
        ('resources/oauth-client.json', source, kind)
        if Path(source).resolve() == client_path else (destination, source, kind)
        for destination, source, kind in analysis.datas
    ]
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True,
                 name='xCAL Community', console=False, icon=str(root / 'src' / 'assets' / 'logo.ico'))
collection = COLLECT(executable, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name='xCAL-community')
