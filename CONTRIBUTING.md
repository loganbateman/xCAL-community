# Contributing

Use synthetic team data and mock Google Drive in automated tests. Never commit
OAuth clients, tokens, `.env` files, telemetry, videos, or real team databases.
Run the publication guard, pyflakes, and pytest before opening a pull request.

Keep local saves durable before network calls. Do not add silent last-writer-wins
overwrites, automatic video downloads, or background services. Storage changes
need two-client, offline, retry, and conflict tests. Do not redistribute AiM SDK
binaries without checking their license. Keep MIT notices on inherited code.
