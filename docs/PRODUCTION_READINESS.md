# Community production-readiness plan

Reviewed October 19, 2026. This is a source review and release plan, not a claim
that OAuth verification, packaging, or live multi-user validation is complete.
Keep production work isolated from the original xCAL app and its credentials.

## Changes completed in this review

- [x] Replace the corrupted window-title dash with an ASCII separator.
- [x] Remove unused Settings configuration-reading/status helpers left by the redesign.
- [x] Normalize the injected Desktop OAuth client's packaged filename to
  `resources/oauth-client.json`, matching `config._default_oauth_client_file`.
  A real packaged sign-in still needs validation.
- [x] Correct README first-launch instructions and document `--preview-local`.

## P0: block public release until resolved

October 22 collaboration update: stable part revision IDs, preserved concurrent
source/merged revisions, deterministic baseline identities, explicit legacy
baseline consolidation and live-capability-based sharing controls are implemented
and regression-tested locally. Shared inventory persistence now lives outside the
Parts view. See [team collaboration](TEAM_COLLABORATION.md) for semantics and live
checks. The release gates below remain open until real-machine evidence exists.

Progress on update/recovery work (October 19): team-document update metadata and
executable chunks have been removed. The updater requires an official GitHub
release digest and rechecks bytes before launch. Atomic token, identity and
settings replacement plus versioned, validated offline recovery are implemented.
See [update security](UPDATE_SECURITY.md) and [backup/recovery](BACKUP_AND_RECOVERY.md).
The broader release gates below still require real-machine and multi-user evidence.

1. [ ] **Establish a trusted update channel.** `src/utils/updater.py` reads
   Previously accepted `app_update` and executable chunks from team-editable app
   documents; that route is now removed and mandatory GitHub digest verification
   has tests for forged metadata, wrong hashes and partial downloads. Finish
   Windows publisher signing/verification once the signing identity is approved.
   Passing a checksum alone is not Windows publisher trust.
2. [ ] **Complete public sign-in readiness.** Confirm the production Community
   client, authorized website ownership, accurate public policies, and Google
   review status. `utils/google_drive_client.py` currently requests full Drive
   scope. Evaluate narrower access against existing-folder/media browsing before
   changing scopes; do not assume selecting a folder limits the OAuth grant.
   Capture actual consent plus live save/sync behavior for the verification demo.
3. [ ] **Validate team lifecycle with real separate accounts.** Exercise owner,
   editor, viewer, revoked access, existing-team folders, failed creation, retries,
   invitations, offline startup, leave and rejoin. Leave currently disconnects
   this device and preserves Drive permissions; document this distinction and
   decide whether actual membership removal is required. Verify no cross-team
   workspace exposure, no lost pending edits, and correct attribution.
4. [ ] **Validate distributed identity safeguards live.** Stable part revision
   IDs and setup references, deterministic new baseline IDs and explicit legacy
   duplicate consolidation now have regression coverage. Validate on two real
   devices, including old numeric-only references and offline fallback queues,
   before relying on them as durable historical references in a public release.
5. [ ] **Validate recovery and durability.** Test crash/restart during catalog
   initialization, upload, conflict resolution and config/token persistence;
   permission loss mid-sync; and export/restore on a clean machine. A documented,
   tested restore UI now creates a separate offline recovery copy. Atomic
   token/identity/settings replacement has interruption tests. Review Windows
   credential storage and log redaction; validate real filesystem/power-loss
   behavior and define an explicit live-team merge workflow for recovered edits.
6. [ ] **Build and test the actual distributable.** Use the Community OAuth client
   outside the checkout, run publication checks, then test the artifact on a
   clean Windows account without Python or developer files. Confirm arbitrary
   downloaded client filenames work, assets load, first login works and a real
   editor can sync. Add a signed installer, uninstall/upgrade tests, and a trusted
   update release. Current `build.ps1` produces a portable directory.

## P1: product and reliability work

- [ ] Hide developer OAuth file selection in normal release mode; provide a
  clear maintainer-only recovery path when a packaged client is missing.
- [ ] Show invitation controls according to verified owner/share capabilities,
  rather than merely the presence of a team profile. Provide member visibility,
  actionable permission errors and clear ownership semantics.
- [ ] Disable conflicting actions during sign-in, invitation and connection
  jobs; deduplicate repeated requests. Audit modal closure while a worker writes.
- [ ] Replace raw exception text with useful user messages and retain redacted
  diagnostics. Make conflict review show fields, authors and choices clearly.
- [ ] Test full journal pagination, large inventories, multi-year sessions,
  rate limits and network timeouts. Add retry/backoff where needed. Profile
  `DriveJournal._index` before adopting incremental cursors or compaction.
- [ ] Test keyboard navigation, 100/150/200% display scaling, long names,
  empty states and offline behavior. Audit source text for other encoding damage.
- [ ] Verify Race Studio versions, optional AiM SDK licensing and behavior when
  optional tools are unavailable; publish supported Windows/Python versions.
- [ ] Add dependency vulnerability review and a reproducible build toolchain.
  The recorded walkthrough's video tooling is local tooling, not an app runtime
  requirement. Keep recordings and credentials out of the public repository.

## Structure review and incremental cleanup order

Current responsibilities:

| Location | Responsibility | Next cleanup |
| --- | --- | --- |
| `qt/views/settings_qt.py` | UI plus team/config/export workflows | Extract connection and workspace lifecycle services; keep dialogs in Qt |
| `qt/views/setup_builder_qt.py` | Setup editor, pickers, runs, history | Extract reusable picker/history dialogs, then run-editing logic |
| `qt/views/parts_qt.py` | Inventory UI, forms, persistence helpers | Move shared inventory persistence into a service; picker currently imports a private helper from this view |
| `database/client.py` | Model API over the compatibility store | Keep public API stable while separating model-specific operations |
| `database/drive_store.py` | Local durable events, merge/conflicts | Keep merge rules independently testable and schema-versioned |
| `database/drive_transport.py` | Drive event I/O | Separate cancellation interface from its current Qt worker import |
| `offline_store.py` | Offline media/inventory queues and caches | Split queue persistence, cache access and transfer orchestration |
| `utils/team_membership.py` | Team profiles and Drive permissions | Centralize lifecycle invariants here rather than only in UI guards |
| `utils/google_drive_client.py` | Auth, identity and Drive operations | Separate credential management from Drive browsing/transfers |

The largest views are roughly 2,400 lines (setup builder) and 1,900 lines
(parts); offline storage is roughly 1,500 lines. Split these in focused changes
with existing behavior tests, not one broad rename/move. Preserve durable local
saves before network work and independent per-run values throughout.

Suggested sequence: trusted updater; credential/config persistence; lifecycle
services; shared inventory service; reusable dialogs; distributed revision
identity; packaging; multi-user beta; release candidate.

## Release gate

- [ ] `python -m pytest -q` passes in the Community environment.
- [ ] `python -m pyflakes src scripts tests` passes.
- [ ] `python scripts/smoke_test_ui.py` passes with isolated data/mocked network.
- [ ] `python scripts/check_publication.py` passes before commit/build.
- [ ] CI runs checks on a clean checkout including all new beta files.
- [ ] Packaged smoke and real two-account sync/recovery tests are recorded.
- [ ] Version, changelog, support contact, privacy policy and release notes agree.
- [ ] All P0 items have explicit evidence; no unresolved data-loss or update-trust issue.

Cloud verification status and external policy compliance were not independently
verified in this code review. Passing unit tests is not a substitute for these
release gates.
