# Team sharing and concurrent edits

Implemented locally October 22, 2026. This is not evidence of Google approval,
Windows signing, or a completed live ownership-transfer test.

## Ownership and administration

The live Google Drive team-folder owner is the team owner. The profile's original
creator is audit information, not authorization. Every management write rechecks
the signed-in Google identity and current Drive capabilities online.

Settings > Team > Manage sharing offers confirmed role changes, removal of named
members, an ownership offer and recipient acceptance. Owners and shared-drive
managers cannot be demoted or removed here. Self-removal, inherited permissions,
group/domain/link grants and shared-drive ownership are managed in Google Drive.
Leaving a team in xCAL only disconnects this device; it does not revoke Drive access.

For My Drive folders, the owner may let **all editors** manage sharing or reserve
sharing for the owner. This follows Drive's `writersCanShare` policy; it is not a
separate selective xCAL admin role. Google organization/account restrictions apply.
Consumer ownership transfer requires acceptance. Transferring the folder does not
transfer ownership of existing child files; do not delete the former owner's
Google account or files assuming that it does. Workspace transfers may require
the organization's Drive administration flow.

## Part revisions

New definitions have stable revision IDs. Numeric labels remain for compatibility
but are not globally unique across devices. New setup links retain the stable ID.
Historical numeric-only references remain unchanged; ambiguous old edits require
reload instead of guessing a source revision.

Journal replay combines independent definition fields. Overlapping fields use
causal order first, then the recorded local timestamp and immutable event ID as
a tie-break. Both source snapshots remain in history, with a stable combined
revision and a visible merge notice. Incorrect device clocks can affect which
concurrent field becomes current; history allows review and restoration.

Stock quantity collisions, deletion/category conflicts and overlapping setup
edits remain explicit conflicts. The separate legacy offline-inventory queue
still blocks a changed remote record for manual review; it does not silently
overwrite it. Stock movements do not rewrite definition history.

## Baselines

New no-ID baseline creation uses deterministic kart/track identity. Local inserts
and scope changes reject another baseline for the same scope. Simultaneous
creation of differing baselines produces one active identity with a recoverable
conflict, rather than two independent baseline rows.

Settings > Workspace > Review duplicate baselines handles older IDs. Sync first,
compare the variants and explicitly choose which one stays active. Consolidation
rejects a stale comparison and records deletion events for the other active rows;
the original immutable journal entries are retained. No automatic bulk deletion
or migration of live team records is performed.

## Live beta checks still required

- Owner and second account: refresh permissions, invite, demote, revoke and retry
  after permission loss. Use only the separate Community test folder.
- With both people's consent, offer and accept folder ownership; verify permissions
  and child-file ownership directly in Drive before removing any old access.
- Two devices: independent and overlapping part edits, newest revision display,
  restore from each source, stock conflicts, and network loss during sync.
- Two devices: create the same baseline offline, reconnect, review the conflict,
  and confirm one baseline plus preserved history on both devices.
- Validate the packaged Windows artifact separately. Local mock/UI tests do not
  establish production readiness.
