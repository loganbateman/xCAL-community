# xCAL Community - first Windows beta

This is an unsigned portable test build, not the final production installer.
Google OAuth verification and Windows signing approval are separate processes;
this build does not claim either approval. Windows or Google may display warnings.
Obtain the ZIP from the maintainer and verify its accompanying SHA-256 checksum.

## Open the app

1. Extract the entire ZIP into a normal folder on your computer.
2. Open `xCAL-community/xCAL.exe`. Keep `_internal` and all other files beside it.
   Do not run only the EXE from inside the ZIP. Python is not required.
3. Choose Create a Team or Join a Team. The Community Google client is included;
   teammates should not create a Cloud project or select a JSON file.
4. Sign in with your own Google account in the browser. If Google denies access
   because the app is testing or unverified, send the exact error to the maintainer.

Your account token and workspace are saved in your Windows user's
`AppData/Local/xCAL-community` directory, outside the extracted app.
Use a separate test Drive folder. Do not use the original Excalibur production folder.

## Invite your friend

Owner:

1. Open Settings > Team and enter the friend's exact Google account email.
2. Choose Invite teammate and review the Editor-access confirmation.
3. Google sends a folder-sharing email. xCAL copies a separate invitation code.
4. Send that code privately to the friend; it is not included in Google's email.

Friend:

1. Extract this same beta and open it under your own Windows account/computer.
2. Choose Join a Team and paste the xCAL invitation code.
3. Sign in with the exact Google account the owner invited.
4. Complete the connection confirmation and let xCAL restart.
5. Confirm the correct team name appears in Settings > Team.

The code does not grant Drive access by itself. An existing team must be left
before another can be joined. Leave currently disconnects that device and does
not remove the account's Drive sharing permission.

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
