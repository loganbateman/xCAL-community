# Community architecture and beta boundaries

## Reused app

The Qt interface, setup model, run snapshots, Drive file browsing/uploads,
Race Studio queue, timestamps, inventory, and offline file queue are preserved.
`database.client` keeps its model-level API; the Supabase client is replaced by
`DriveStore`, a narrow compatibility adapter backed by local SQLite and a Drive
journal. Supabase is not in requirements and is not contacted.

## Catalog journal

One immutable JSON file per edit contains a schema version, unique ID, collection,
record key, parent change IDs, previous record, and resulting record. Uploads
append files; they never replace a shared SQL database or mutable catalog blob.
Parents upload before their children. Lost upload responses can be retried using
the same event identity. Readers reject mismatched identities and malformed DAGs.

Replay uses parent-before-child ordering with stable ID ordering for concurrent
branches, so clients with the same journal converge. Three-way merging combines
independent fields and ID-keyed run/history lists. Overlapping writes, deletes
versus edits, and concurrent stock changes retain recoverable conflicts. SQLite
rebuilds are transactional; an invalid remote journal does not replace the cache.
An explicit resolution adds a new event covering all known heads, retaining the
original changes. Subsequent conflicting edits still require review.

The original setup audit trail also remains intact. Editor labels describe the
client's identity context; they are not a server-enforced signature. Anyone with
Drive Editor permission can change journal content manually, so membership and
folder permissions must be trusted. No public link permissions are created.

## Isolation and lifecycle

Configuration/tokens live in the community user-data root. Session caches,
offline packages, and catalog databases are namespaced by a hash of the selected
Drive root. The original team app's cache and Supabase project are not used.
There is no daemon, tray auto-run, or cloud worker after the application exits.
An in-flight request may need to finish before cooperative shutdown completes.

## Known limitations before wider beta

- Real multi-account Drive/OAuth and viewer/editor permission tests remain.
- Google OAuth still needs a Desktop client and uses restricted full Drive scope.
- Conflict review currently shows the full record comparison, not a polished
  field-by-field visual merge editor.
- Drive has no SQL transactions across independent records. Local stock operations
  are transactional, but uploads of a part and its movement history are separate
  events and can be temporarily incomplete on another device.
- The full event journal is indexed each sync; known payloads are not downloaded
  again. Snapshot compaction, Drive Changes API cursors, pagination load tests,
  and larger-team performance work are planned.
- Journal folder initialization should be done by one owner, not simultaneously
  by multiple new clients. Ambiguous duplicate catalog folders fail closed.
- No server-side uniqueness constraints: concurrent creation of two different
  baseline IDs for the same kart/track needs a duplicate-review workflow.
- Inventory definition revision numbers inherit the original numeric model.
  Concurrent branches can have the same numeric revision, even though their
  immutable event IDs differ. Stable distributed revision identities are needed
  before treating linked part-revision references as production-grade.
- No Supabase-to-Drive migration tool has run. The schemas/models were reused,
  not your team's records. Local-only workspace adoption into a Drive is not yet
  automatic.
- Windows Race Studio automation depends on the installed application's native
  dialogs. Its inherited fixes still need beta coverage across Race Studio versions.
- The optional AiM SDK is not included; distribution/licensing must be reviewed
  before bundling it in public installers.
- Portable packaging is provided; an independent signed installer/update channel
  and real packaged end-to-end tests remain before a production release.
