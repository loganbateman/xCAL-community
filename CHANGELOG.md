# Changelog

## Unreleased - OTK guide baseline draft

- Give every Community team an editable OTK dry-baseline draft sourced from the
  OTK chassis setup guide and the reviewed tuning values. Saving creates that
  team's own universal baseline without requiring a driver, kart, or track; no
  values are published or synced until a user reviews and saves the draft.

## Unreleased - Tire pressure inputs

- Remove duplicate Front/Rear Tire Pressure inputs from the chassis sections;
  keep per-tire Cold/Hot pressures and preserve previously saved legacy values.

## Unreleased - Split-screen layout

- Group compact run controls into a centered Previous / Run / Next row, with
  equally sized Add / Remove / Notes / color-key actions below instead of uneven wrapping.

- Allow Community windows down to 640 × 480 for Windows snap/split-screen use.
- Automatically collapse navigation in tight spaces, restoring the user's
  expanded/collapsed preference when space returns.
- Reflow setup fields, identity inputs, run controls and common page toolbars;
  keep the same live widgets and preserve unsaved edits and keyboard focus.
- Stack tire panels without the kart image in narrow editors; restore the
  diagram on wider windows. Hide the secondary session rail when space is tight.
- Keep comparison selections intact while adapting their controls to narrow pages.

## Unreleased - Run navigation and baseline comparison

- Keep run conditions, run feedback and setup-sheet notes uncolored while still
  tracking unsaved changes. Baseline/previous-run colors apply to setup fields only.
- Clarify that the checklist is shared across runs; disabled checkboxes explain
  the missing kart/session requirement. Add real mouse-click and persistence tests.
- Refresh an open Sync Queue when background catalog sync drains it, preserving
  active progress. Replace internal document/record IDs with readable queue names.

- Run controls now fit on one line with a compact boxed comparison key.
- Community Add Run and Remove Run actions are pinned in that top bar, with
  baseline removal protection and the existing confirmation preserved.
- Run Log / Notes centers the feedback editor rather than the heading.
- Settings > Workspace can add, rename, move, hide and restore custom tuning
  fields or pickers linked to existing Parts categories. Stable keys and hidden
  run values/part references are preserved; standard fields stay unchanged.

- Pin the selected setup run and Previous/Next controls above the editor.
- Label Run 1 as the Baseline Run and add a prominent Run Log / Notes shortcut.
- Highlight later-run differences from Run 1 in yellow and differences from the
  previous run in red, including part revisions and feedback. Both comparisons
  use yellow fill with a red border. A pinned top-right key explains the colors;
  comparisons persist after saving. Run numbers, dates and times are excluded.

## Unreleased - Collaboration safeguards

- Added confirmed Drive sharing controls using the live folder owner and sharing
  capabilities, plus owner-controlled editor administration and ownership offers.
- Added stable part revision IDs, combined concurrent definitions, preserved
  overlapping source revisions and merge notices. Stock conflicts stay explicit.
- Added deterministic new baseline identities and stale-safe, explicit review of
  legacy duplicates. Original journal history remains preserved.
- Extracted shared inventory persistence and the sharing dialog from view code.
- These are local source changes; live transfer and packaged multi-device checks
  remain release gates. No existing Drive permissions were changed automatically.

## Unreleased — Maintenance and session types

- Settings > Team now lists current Google Drive sharing entries with names,
  emails and access roles, plus refresh and offline/permission error states.

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
