"""Package the tested portable app; publish nothing and include no user data."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import config  # noqa: E402


def main():
    app = ROOT / 'dist' / 'xCAL-community'
    if not (app / 'xCAL.exe').is_file():
        raise ValueError('Build the app before packaging.')
    output = ROOT / 'dist' / 'releases'
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    name = f'xCAL-Community-{config.APP_VERSION}-beta-{stamp}-windows-x64'
    archive_path = output / f'{name}.zip'
    source = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    metadata = {'version': config.APP_VERSION, 'channel': 'unsigned-beta',
                'built_utc': stamp, 'source_commit': source, 'source_has_local_changes': dirty,
                'format': 'portable-directory', 'signed': False}
    forbidden = {'settings.env', 'google_token.json', 'google_identity.json', '.env', 'credentials.json'}
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in app.rglob('*'):
            if not file.is_file():
                continue
            relative = file.relative_to(app)
            if file.name.casefold() in forbidden or file.suffix.lower() in {'.db', '.sqlite3', '.log'}:
                raise ValueError('User state found in build output; packaging stopped.')
            archive.write(file, 'xCAL-community/' + relative.as_posix())
        archive.writestr('xCAL-community/BUILD-INFO.json', json.dumps(metadata, indent=2))
        archive.write(ROOT / 'docs' / 'BETA_QUICKSTART.md', 'xCAL-community/START-HERE.md')
        archive.write(ROOT / 'LICENSE', 'xCAL-community/LICENSE')
    with archive_path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    archive_path.with_suffix('.zip.sha256').write_text(f'{digest}  {archive_path.name}\n', encoding='utf-8')
    print(archive_path)
    print(f'SHA-256: {digest}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
