# Changelog

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
