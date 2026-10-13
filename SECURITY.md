# Security

Do not put credentials or real team data in GitHub issues. Report security
problems privately to the repository owner using GitHub's private reporting
channel if enabled, or another private channel before public disclosure.

The preview uses Google Drive folder access as its trust boundary. Any Editor
can alter or remove files, including the journal. xCAL does not make the folder
public or grant teammates permissions automatically. Tokens live locally and
are never bundled in builds. Local-only edit attribution is unverified.

The current OAuth scope is broad; see the setup guide before authorizing it.
The publication guard is a basic safety check, not a full secret scanner.
