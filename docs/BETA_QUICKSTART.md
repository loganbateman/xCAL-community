# xCAL Community - first Windows beta

This is an unsigned beta build, available as a Windows installer and portable ZIP.
Google OAuth verification and Windows signing approval are separate processes;
this build does not claim either approval. Windows or Google may display warnings.
Obtain the ZIP from the maintainer and verify its accompanying SHA-256 checksum.

## Open the app

1. Run the Windows installer, or extract the entire portable ZIP into a normal
   folder. Windows may show an unsigned-app warning during this beta.
2. Launch xCAL Community from the Start menu. For the ZIP, open
   `xCAL-community/xCAL Community.exe` and keep `_internal` beside it. Python is not required.
3. Sign in with Google, then select an existing team or create a team. The Community Google client is included;
   teammates should not create a Cloud project or select a JSON file.
4. Use your own Google account in the browser. If Google denies access
   because the app is testing or unverified, send the exact error to the maintainer.

Your account token and workspace are saved in your Windows user's
`AppData/Local/xCAL-community` directory, outside the extracted app.
Use a separate test Drive folder. Do not use the original Excalibur production folder.

## Invite your friend

Owner:

1. Open Settings > Team and enter the friend's exact Google account email.
2. Choose Invite teammate and review the Editor-access confirmation.
3. Google sends the teammate a folder-sharing email.
4. Ask the friend to sign in to Community with the invited Google account.

Friend:

1. Extract this same beta and open it under your own Windows account/computer.
2. Accept the Google Drive sharing email, then open Community.
3. Sign in with the exact Google account the owner invited.
4. Choose the shared team from the team profiles; refresh teams if it is missing.
5. Confirm the correct team name appears in Settings > Team.

There is no invitation code. Google Drive sharing grants access. Switch team
changes profiles, and Leave disconnects this device without removing the
account's Drive permission. Sync pending edits before switching or signing out.

## Updates

This build follows the Community beta release channel. Use Settings > Workspace
> Updates > Check for updates. Installer downloads are verified against GitHub's
SHA-256 digest before launch. The first upgrade from an older preview may need
the installer downloaded manually because earlier previews skipped beta releases.

## Two-account acceptance test

- Owner invites friend; friend sees the sharing email and successfully joins.
- Owner creates one clearly named test part; friend refreshes and sees it.
- Friend creates a second test part; owner refreshes and sees it.
- Both close/reopen and verify records are retained.
- Test an offline edit followed by reconnection; no pending work should vanish.
- Export a backup and restore into a separate offline copy.
- Record wrong-account, permission-denied or sync errors without sending tokens.

The beta's local automated tests use mocked Google APIs. Actual second-account
join/sync must be observed before calling this test successful. No invitation,
release upload or publication is performed by the build scripts.

Telemetry/Race Studio integration is optional and requires its own installed
tools. Downloaded media and team records are not bundled in this beta.
