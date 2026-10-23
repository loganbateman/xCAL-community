# Local community beta — October 13, 2026

Historical handoff. For the current beta use [0.2.1 release notes](RELEASE_0.2.1.md)
and [tester instructions](BETA_QUICKSTART.md). The state below is from October 13,
not a current OAuth or release-status report.

## Confirmed direction

- `xCAL-community` remains the repository name; the app is xCAL.
- Normal Google sign-in, no manual OAuth client setup for each team owner.
- A separate app-owned Google client/project, never the original team app's.
- First-login Settings window: Create a Team / Join a Team.
- Owner invitations grant named-account Google Drive Editor access after explicit
  confirmation. Google Drive permission is the team membership proof; xCAL does
  not use separate join codes.
- Keep original kart fields. Add team-specific parameters in Front End, Rear End,
  Engine / Drivetrain / Carb, Tires & Wheels, and Session.

## Implemented locally

- Native read-only Drive folder browser: My Drive, Shared with me, Shared drives.
- First-login Create/Join dialog and owner-managed invitation flow in Settings.
- Separate-folder team creation and permission-checked joins, with restart into
  that folder's isolated workspace after connection.
- Add/hide custom setup parameters with stable IDs, labels, units, text/number
  types, per-run snapshots, comparisons, and readable history. Hidden values stay
  preserved; original fields cannot be replaced or removed by custom keys.
- Cloud sync no longer holds the local database lock during network requests.
- Separate VS Code workspace, debug launch, test task, and community virtualenv.

## Authentication blocker

There is no registered community-owned Google sign-in client yet. The local
developer setting now points only to the community's own missing client file.
The temporary reference to the original client's file was removed, and no
community token or identity file exists. No real Drive folder was connected,
team created, or invitation sent during this turn.

Create/Join screens and custom parameters can be tested locally. Live Google
sign-in and folder selection stay disabled until the maintainer registers the
community's own Desktop client. This is a one-time app-maintainer task, not a
team/member onboarding requirement. Public rollout must publish/verify that
client and settle the scope/privacy/support plan; a Testing client can still
have Google's test-user restrictions.

## Next session

Open `xCAL-community.code-workspace`. Use the **xCAL Community (isolated)** debug
configuration or `python src/main.py --onboarding`. The Settings page opens first while no
team is connected; **Create a Team or Join a Team…** opens the onboarding window.

The latest source changes are intentionally local beta work. Do not use the
older portable build to test these new screens until it is rebuilt.

Validation: 232 tests plus 3 subtests pass locally; lint, publication guard, and
the isolated full-UI startup/shutdown smoke check pass. Tests use mock Drive APIs;
they are not a claim of real OAuth or invitation delivery validation.

Before live collaboration testing: register/provide the separate community app
client, choose an empty separate test folder, and use a second invited Google
account. Do not change the original app or its Supabase project.
