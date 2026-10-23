# Community update trust

Community 0.2.1 follows the beta channel and reads the official repository's
release list, allowing prereleases with numeric version tags while rejecting
drafts. Stable builds reject prereleases. Both channels require official asset
URLs and digests. The installer name is `xCAL-Community-Setup-VERSION.exe`;
the former `xCAL-Setup-VERSION.exe` remains accepted for compatibility.

The app checks only the HTTPS GitHub release API for
`loganbateman/xCAL-community`. Team catalog documents, update manifests stored in
Drive, and executable chunks in app documents are not consulted.

Automatic installation requires a release allowed by the app's update channel, a correctly named installer,
an official repository release-download URL and GitHub's SHA-256 asset digest.
Releases without a digest can be opened for manual review but cannot be installed
through the updater. The app re-fetches release information before downloading
and rejects changed or caller-supplied metadata.

Downloads use a unique temporary directory and are bounded to 250 MiB. Redirects
are restricted to HTTPS GitHub and its known release-asset delivery hosts. Failed,
empty, oversized, interrupted and checksum-mismatched downloads are removed.
Only downloads verified in the current app process can be launched; their content
is hashed again immediately before execution.

This trusts repository release access and GitHub's TLS/API delivery. Protect the
maintainer GitHub account and release permissions. A digest from GitHub provides
an independent boundary from team-edited Drive records; it is not a Windows
publisher signature. SignPath approval, signed release artifacts and publisher
verification remain part of the production release work. No signer identity has
been invented or configured before that approval.

The automated tests never download or execute a real installer. They cover team
manifest isolation, untrusted URLs, redirects, absent hashes, forged metadata,
interrupted streams, failed checksums and file tampering before launch.
