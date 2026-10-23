# Community development and packaging

This page is for contributors and release maintainers. App users should use the
[quick start](BETA_QUICKSTART.md); they do not configure OAuth clients.

## Local development

Use Python 3.12 and this repository's virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt pytest pyflakes pyinstaller
python src/main.py --preview-local
```

`--preview-local` opens the local UI without first-login onboarding. Source
checkouts do not contain the packaged Community client. Keep all credentials
outside the checkout and separate from regular xCAL.

## Maintainer Google configuration

Maintain a dedicated Community Google Cloud project with the Drive API enabled.
Use its Desktop OAuth client, branding and audience configuration. Store the
downloaded client JSON outside the checkout. Never reuse the private xCAL client,
tokens or identity cache.

Inject the Community client when building:

```powershell
.\build.ps1 -OAuthClient C:\private\community-client.json -Package -Installer
```

The application client identifies Community. Users sign in normally; their tokens
stay in their own Community app-data directory. End users never select this file.

The current Drive browser uses full Drive access to browse existing folders.
Selecting a folder does not narrow the token's OAuth scope. Confirm Google's
current review requirements and project status before wider distribution; build
success is not proof of verification. Changing to narrower scopes requires a
corresponding folder-selection redesign.

## Checks

```powershell
python scripts/check_publication.py
python -m pytest -q
python -m pyflakes src scripts tests
python scripts/smoke_test_ui.py
python scripts/smoke_test_ui.py --recovery
```

Builds run these checks, package the app, test packaged startup and produce
installer/portable checksums. Keep user records and credentials out of releases.

Community uses a local catalog and team-owned Drive journal. Preserve durable
local saves, independent run snapshots and explicit conflict resolution. API
tests use mocks. Live tests use a separate team folder.

See [architecture](ARCHITECTURE.md), [release readiness](PRODUCTION_READINESS.md),
[update security](UPDATE_SECURITY.md) and [backup/recovery](BACKUP_AND_RECOVERY.md)
for engineering details. Google approval, Windows signing and clean-machine
acceptance must be recorded separately from automated checks.
