"""Fail before public commits/builds if credentials or private data are tracked.

This complements (not replaces) GitHub secret scanning and human review.
Only names are printed when a problem is found, never secret values.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {'.env', 'credentials.json', 'oauth-client.json', 'google_token.json', 'google_identity.json'}
FORBIDDEN_SUFFIXES = {'.xrk', '.ibt', '.mp4', '.mov', '.db', '.sqlite3', '.exe', '.dll'}
PATTERNS = [re.compile(pattern) for pattern in (
    r'GOCSPX-[A-Za-z0-9_-]{12,}',
    r'gh[pousr]_[A-Za-z0-9_]{20,}',
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'sb_(?:secret|publishable)_[A-Za-z0-9_-]{16,}',
    r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}',
    r'\d{8,}-[A-Za-z0-9_-]{15,}\.apps\.googleusercontent\.com',
)]


def main():
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT, check=True, capture_output=True)
    problems = []
    for name in result.stdout.decode('utf-8').split('\0'):
        if not name:
            continue
        path = ROOT / name
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(name)
            continue
        if path.suffix.lower() in {'.png', '.ico'}:
            continue
        content = path.read_text(encoding='utf-8', errors='replace')
        if any(pattern.search(content) for pattern in PATTERNS):
            problems.append(name)
    if problems:
        print('Publication blocked; review these files:')
        print('\n'.join(sorted(set(problems))))
        return 1
    print('Tracked-file publication safety checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
