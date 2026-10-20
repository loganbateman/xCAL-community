# xCAL

An open-source kart-team desktop workspace for Purdue Grand Prix and other kart
teams. Setups, per-run changes, notes, telemetry, videos, parts, and comparisons
stay together. Built with Python and PySide6; licensed under MIT.

**Community preview, not yet a production release.** This is a clean-history
community edition derived from xCAL v0.5.36. It has no bundled team credentials,
roster, Supabase project, access password, or private data. The original team app
continues using Supabase and has not been migrated.

## Storage

- Google Drive holds shared setups, run notes, parts, naming settings, XRKs, and
  videos. No Supabase account or database administration is required.
- SQLite is a durable local cache, not a database file passed around Drive.
- Each catalog edit is an immutable JSON change in `.xcal-catalog-v1` inside the
  owner's selected shared Drive folder. Clients merge non-overlapping edits.
- Conflicts preserve both changes and appear in Settings for explicit review.
  Drive Editor permission is the access boundary; there is no central xCAL server.
- Videos list online and download only when opened or through Download All.
  Closing the app cancels work cooperatively; it is not a background service.

The journal backend is covered by fake-Drive, two-client tests. Real team-account
OAuth, shared Drive permissions, long-running usage, and packaged builds still
need beta validation before recommending this to other teams.

## Run locally

Python 3.12 is the tested version. Windows is required for Race Studio automation.

```powershell
git clone https://github.com/loganbateman/xCAL-community.git
cd xCAL-community
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python src/main.py
```

The first launch opens Create a Team / Join a Team. Developers can use
`python src/main.py --preview-local` to explore without Google credentials.
To collaborate, follow [Google Drive setup](docs/GOOGLE_DRIVE_SETUP.md).
Configuration and tokens are stored outside the checkout, in a separate
`xCAL-community` user-data directory. Each selected Drive has an isolated cache.

## Included features

- Baseline setups, comparisons, parts inventory, and stock/revision history.
- Run 1 as baseline; later runs hold independent setup snapshots and notes.
- Session navigation with event names first and a Setup/Notes tab.
- Batch telemetry uploads, individual Race Studio opening, and serialized Open All.
- On-demand videos, offline listings, sync queue, timestamps, and editor history.
- Exportable private workspace backups and recoverable shared-edit conflicts.

XRK acquisition timestamps use AiM's optional Windows SDK. It is not redistributed
in this repository. MOV/MP4 metadata timestamps remain available without it.

## Development

```powershell
python -m pip install pytest pyflakes pyinstaller
python -m pytest -q
python -m pyflakes src scripts
python scripts/check_publication.py
.\build.ps1
```

See [architecture and current limitations](docs/ARCHITECTURE.md),
[production-readiness checklist](docs/PRODUCTION_READINESS.md),
[backup and recovery](docs/BACKUP_AND_RECOVERY.md),
[update security](docs/UPDATE_SECURITY.md),
[contributing](CONTRIBUTING.md), and [security](SECURITY.md).
