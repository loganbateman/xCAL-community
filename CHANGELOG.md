# Changelog

## Unreleased — Maintenance and session types

- Added setup checklists and maintenance with timestamped completions.
- Added Settings → Maintenance for editable default and Practice, Qualifying,
  and Race intervals, measured independently in sessions or days.
- Added session type selection, classification of existing sessions, and a
  session type filter. Community saves schedules and classifications to its own
  offline catalog and team Drive journal.

## 0.1.1 - Community beta hotfix

- Fix setup saves rejecting Driver, Kart/Chassis and Track records created from
  the picker during the same edit. Validation and selection now share the current
  inventory list instead of separate startup snapshots.
- Add regression coverage for Ctrl+S on session setups and baselines, preserving
  the selected inventory IDs/revisions and rejecting genuinely unlisted values.

## 0.1.0 — Community development preview

- Clean-history MIT community edition derived from the team app at v0.5.36.
- Removed bundled credentials, team access gate, legacy folder IDs, team kart
  mappings, team logos, and the Supabase runtime dependency.
- Local SQLite catalog with immutable, team-owned Drive journal synchronization.
- Field/run-level three-way merge, recoverable conflicts, explicit resolutions,
  parent-ordered uploads, and safe retry of lost upload responses.
- Drive connection Settings, conflict review, and private workspace backup export.
- Separate community/team caches and tokens; no migration of the existing app.
- Preserved per-run setups, compact session UI, on-demand videos, and the direct
  serialized Race Studio batch opener.
- Added publication safety checks and Windows CI. No production release yet.
