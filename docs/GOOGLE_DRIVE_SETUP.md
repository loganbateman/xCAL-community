# Connect any team's Google Drive

Public builds include the dedicated xCAL Community Desktop OAuth client so every
user gets normal Google sign-in without choosing a JSON file. There are no shared
Google passwords or embedded user tokens. Google verification is still pending,
so beta users may see Google's unverified-app warning or testing limits.

## Public maintainer setup

1. Create a dedicated Google Cloud project for xCAL Community and enable the
   Google Drive API. Do not reuse the original xCAL project or client.
2. Configure Google Auth Platform branding and audience. Use an External audience
   for Google accounts outside one Workspace organization.
3. Create an OAuth client of type **Desktop app** and download its JSON outside
   this checkout.
4. Build with `./build.ps1 -OAuthClient C:\private\community-client.json`.
   The client configuration is injected into the packaged app; it is never
   copied into the source tree. Each user's refresh token remains private in
   their own xCAL Community user-data directory.
5. Publish the consent configuration for production use. Because the current
   folder browser requests the restricted full-Drive scope, complete Google's
   required OAuth verification before calling the app open to arbitrary users.

## Team owner

For a maintainer-built beta with the Community client included:

1. Extract the full portable ZIP and open `xCAL.exe`.
2. Choose **Create a Team**, sign in and choose/create a dedicated test folder.
3. Confirm connection. xCAL initializes the catalog and restarts into that team.
4. In Settings > Team invite the friend's named Google account with Editor access.
5. The teammate accepts Google's sharing email and logs in with that Google account. xCAL discovers the team automatically.

Team owners do not register their own Google client or choose JSON files. The
external client file named in the build command is maintainer-only build input;
it is never copied into the source tree or selected inside the application.

## Teammates

Each teammate extracts the same beta, chooses **Join a Team**, pastes the code
and signs in with the exact Google account the owner invited. The owner must
grant that account access to the folder. If the Community project's OAuth status
still restricts access, the maintainer must resolve that Google-side restriction;
joining an xCAL team cannot bypass it. Public verification is not claimed here.

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
