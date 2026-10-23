# xCAL Community 0.2.1 - Windows beta

An unsigned Community beta for invited testers. This is not a claim of Google
verification, Windows publisher signing, or completed production acceptance.

## Install

Download `xCAL-Community-Setup-0.2.1.exe` for the Windows installer, or the portable
Windows x64 ZIP. Compare its SHA-256 checksum with the accompanying checksum file.
The installer provides Start-menu and optional desktop shortcuts and an
uninstaller. Python is not required. Keep `_internal` beside `xCAL.exe` for the ZIP.

The Community Desktop Google client is bundled at build time; users should never
provide OAuth JSON files. Each user's tokens and team data stay in their own
Community app-data directory. No user tokens or team records are bundled.
Follow [the tester guide](BETA_QUICKSTART.md) for sign-in and invitations.

## Changes

- Google account first, remembered identity, sign-out and team profiles.
- Direct member role editing and confirmed removal; invitations follow live
  Drive capabilities. Owners control whether editors can share.
- Durable saves trigger debounced sync, plus startup and three-minute checks.
  Readable queue labels and connection requests that avoid overlapping work.
- Faster searches, reusable picker catalogs and responsive split-screen layouts.
- Compact Settings; maintenance lives in Workspace.
- Visual field editor with click-to-edit captions, custom part types and typed
  attributes, field removal/restoration and drag-to-reorder within sections.
- Cage Spring Preload next to Cage, with 1-2 turns in the OTK baseline.
- Independent runs, baseline/previous highlights and a shared setup checklist.
- Verified GitHub beta update downloads with consistent installer filenames.

## Known limits and next acceptance checks

- Windows signing is deferred; Windows may show unsigned-app warnings.
- Google review status is not established by this build. Google's sign-in
  restrictions remain in effect until the applicable approval is complete.
- Clean-Windows-account install/upgrade/uninstall and live OAuth still need
  tester acceptance; packaged smoke checks do not establish these.
- Test two-device offline/concurrent edits, revoked permissions, team switching
  and backup/recovery on a separate test folder before wider rollout.
- Older previews may need a manual installer upgrade because their updater
  ignored prereleases. Version 0.2.1 follows the beta channel.
- Team devices should use this version to understand custom types and layouts.
  Previously saved custom fields remain intact.

SignPath remains deferred. The original private xCAL app is not part of this release.
