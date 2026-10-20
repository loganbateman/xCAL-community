# Workspace backup and recovery

In Settings > Workspace, use **Export backup** to create a ZIP outside the active
workspace. The archive contains that workspace's catalog, caches, queued edits
and downloaded files. It does not contain Google tokens, identity caches, OAuth
configuration, or account passwords. Store it privately: it contains team data.

Each database is captured through SQLite's backup API, including committed WAL
transactions. File hashes and sizes are recorded in a versioned manifest. Export
writes to a staging file and replaces the destination only after completing the
archive. If exporting fails, an existing backup is retained. Stop editing and
transferring files while exporting when you need one consistent point in time
across separate databases and media files; this is not a global multi-file
transaction or a cloud backup of files that have not been downloaded.

## Restore

1. Open Settings > Workspace > **Restore backup**.
2. Select the ZIP and a parent directory for the recovered copy.
3. Confirm creation of a new offline copy. The active workspace is not replaced.
4. xCAL verifies archive paths, sizes, hashes and SQLite integrity, then creates
   a new `xCAL-recovery-<id>` directory. Invalid/incomplete archives are not opened.
5. Choose **Open** when prompted to inspect the copy in a separate xCAL window.

Recovered Google access and synchronization are disabled. Pending edits remain
in the recovery databases. Known offline-media paths are rewritten to the new
location. Recovery does not change team permissions or merge recovered events
into a shared journal. Close the recovery window and launch xCAL normally to
return to the original workspace.

The new backup format requires `xcal-backup.json`. ZIP exports made before this
feature lack validation metadata and are deliberately not automatically imported.
Re-export from the original workspace with the current app. Keep any old ZIP
when the original workspace is unavailable; manual recovery is a separate task.

To reopen an existing recovery copy from a source checkout, start a fresh
PowerShell terminal and use the Community environment:

```powershell
$env:XCAL_DATA_DIR = 'C:\path\to\xCAL-recovery-example'
.\.venv\Scripts\python.exe src/main.py --preview-local
```

The copy's `settings.env` enables recovery mode on startup. Close this terminal
afterward so the environment override is not reused for normal launches.

## Failure and security boundaries

- Archive checks detect corruption and unsafe extraction paths. Hashes inside
  the ZIP do not authenticate who made it; restore only backups you trust.
- Credentials, account identity and settings use flushed atomic file replacement.
  They remain local files under the user's OS permissions, not an encrypted
  Windows credential vault. File replacement protects against partial writes;
  storage hardware and power-loss guarantees depend on the filesystem/device.
- Offline recovery is for inspection, local editing and re-export. Reconnecting
  or merging it into a live team requires an explicit reviewed recovery workflow.
- Tests use temporary workspaces and fake network responses; no live team data
  is imported or overwritten by the automated checks.
