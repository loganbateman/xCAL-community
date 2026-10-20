# xCAL Community working context

- Work in this repository, not the original sibling `../xCAL` team app.
- The original team app keeps Supabase. This community app uses its own local
  workspace and a team-owned Google Drive journal; do not migrate production data.
- Authentication must be fully separate. Never borrow the original app's OAuth
  client, tokens, identity cache, or configuration. The temporary local client
  reference was removed; no community Google tokens have been created.
- The community app still needs its own Google Cloud / Desktop OAuth client
  registration before live sign-in. Public onboarding should be normal Google
  sign-in with no manual OAuth-client setup or Google test-user list for teams.
  Do not claim that shared public sign-in is registered or verified yet.
- First login should offer Create a Team / Join a Team. Owner invitations use
  actual named-account Drive permissions, never a code that bypasses access.
- Keep original kart setup fields, and allow additional parameters by section.
  Run 1 is baseline; later runs retain independent values and notes.
- Live beta tests must use a separate user-chosen test folder and app client,
  never the original team's production folder. API tests use mocks.
- Use the community `.venv`. Test with `python -m pytest -q`, lint with pyflakes,
  and run `scripts/smoke_test_ui.py`. The VS Code workspace/debug/tasks are local
  to this repo. Current source changes are local beta work, not yet pushed.
- No automatic video downloads, background service, or silent overwrite of
  conflicting shared edits. Keep saves durable before network work.
- Credentials belong outside the checkout in the community user-data root.
  Run `scripts/check_publication.py` before any public commit or build.
