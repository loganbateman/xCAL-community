# Morning handoff — October 13, 2026

## Original team xCAL

v0.5.36 was published successfully to the original private GitHub repository.
The installer, portable ZIP, and installed-app updater manifest were verified.
The team app keeps Supabase; no team database migration has been performed.
Included: direct serialized Race Studio imports, Run 1 baseline / independent
later-run setups, event-name-first lists, and Setup/Notes. Tests: 180 passed plus
3 subtests. The updater now uses smaller immutable version-specific chunks.

## New community edition

Repository: https://github.com/loganbateman/xCAL-community

The app is still called xCAL. This is an MIT development preview with clean Git
history, not a public copy of private repository history. It includes the existing
general UI/features and replaces the Supabase client with an offline-first local
catalog and immutable shared Drive journal. Setups, run notes, parts, and naming
settings can share the same chosen Drive as data and videos.

Separate local data prevents accidentally reading the original team cache. Team
credentials, OAuth tokens/client, private folder IDs, logos, access gate, and kart
number defaults were excluded. The source runs in a fresh environment without
Supabase installed. Publication guard, lint, inherited UI tests, two-client
catalog tests, transport tests, and full UI smoke checks are included.

Validation so far: 203 tests plus 3 subtests pass in the clean community virtual
environment. Lint and the publication guard pass. The portable Windows app builds,
and its isolated packaged startup/shutdown test passes. Initial GitHub CI also
passed; portable preview artifacts are built through the manual build workflow.
The local executable is `dist/xCAL-community/xCAL.exe`; run it normally to try
the onboarding screen, or use `--smoke-test` for an isolated automatic check.

Real OAuth/multi-account Drive writes have NOT been tested against your team
account. No new team folder was created and no private setup was uploaded.
Do not distribute this preview as production-ready yet. See ARCHITECTURE.md for
concurrency, scaling, inventory-revision, and packaging boundaries.

## Decisions for the morning

1. For initial beta, should each team owner configure a Desktop OAuth client,
   or do we start the Google verification work for one shared xCAL client? The
   latter is the smoother eventual experience but requires a maintained Cloud
   project, privacy/support pages, and a scope/verification plan.
2. Which empty, separate Drive folder and second Google test account should we
   use for the first real collaboration beta? Do not use the production team
   folder for these tests.
3. Should other teams start with the current kart setup fields, or should owners
   also be able to customize field names/units and hide unused fields?
4. Is `xCAL-community` a good repository name while retaining xCAL as the app name?

## Next engineering work

- Real owner/editor/viewer testing, auth refresh/revocation, and Drive outage tests.
- Polished field-level conflict review instead of whole-record JSON previews.
- Stable distributed inventory revision identities, baseline duplicate review,
  and local-only workspace adoption/import previews.
- Google Picker + narrower permissions, shared OAuth verification strategy.
- Journal compaction, changes cursors, and larger-team performance tests.
- Independent signed installer and public update channel once the beta is ready.
