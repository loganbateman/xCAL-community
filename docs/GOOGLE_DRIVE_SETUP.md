# Connect any team's Google Drive

This preview uses a team-owned Desktop OAuth client. There are no developer-owned
cloud accounts, paid database requirements, shared Google passwords, or embedded
tokens. The setup still requires a one-time Google Cloud configuration; a
verified shared xCAL OAuth client is a future decision, not something this build
pretends to provide.

## Team owner

1. Create a dedicated xCAL folder in Google Drive. Share it with teammates who
   need access. Editors can upload, edit, and delete team files; viewers cannot
   contribute catalog edits. Do not share this folder publicly.
2. In Google Cloud Console create a project, enable Google Drive API, and configure
   Google Auth Platform consent. Use Internal only for an eligible Workspace
   organization. For External testing, add the teammates as test users.
3. Create an OAuth client of type **Desktop app** and download its JSON. Do not
   choose a service-account key or a Web application client.
4. Launch xCAL → Settings. Paste the shared folder URL, choose that JSON, then
   click **Save connection and initialize catalog**. Review the confirmation.
5. Sign in through Google's browser page. xCAL creates `.xcal-catalog-v1` inside
   the chosen folder and saves the connection privately on this device.
6. Restart xCAL. New shared catalog changes go to that folder, not Supabase.

The Desktop OAuth client JSON can be supplied privately to teammates. A Desktop
client is not a server-side secret; individual refresh tokens are private and
must never be shared. Do not post client configuration in this repository.

## Teammates

Each teammate installs/runs xCAL, chooses the same folder and client JSON, and
signs in with their own account. The owner must grant that account access to the
folder and, in testing mode, include it in the OAuth test-user list.

## Important OAuth limitations

The inherited folder-browser integration currently requests the broad `drive`
scope because it enumerates existing folders/files. This is a restricted scope,
not a folder-scoped permission grant. The app confines its catalog writes to the
selected folder, but the token itself is broader. External testing refresh tokens
for Drive scopes can expire after seven days. Wider public distribution may need
Google verification and, depending on scope/use, additional assessment.

The intended lower-permission path is `drive.file` with Google Picker/UI grants;
simply replacing the scope would break arbitrary existing-folder browsing. A
team-owned client is the preview path; seamless public OAuth is still work ahead.

Official references:

- [Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Google Picker for desktop](https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker)

## Offline and switching teams

Edits are saved on disk and retried while xCAL is open. Videos are not automatically
downloaded. Do not manually alter or delete journal JSON files. Use Settings to
resolve conflicts, and keep private backups of important sessions.

Changing folders requires restarting the app; team caches remain separate.
Local-only work is retained in its original workspace, not automatically migrated
into a newly selected Drive. Switching from an existing team with pending changes
is blocked until those changes are dealt with. Export a workspace backup before
switching. Automatic Supabase import is not implemented or run.
