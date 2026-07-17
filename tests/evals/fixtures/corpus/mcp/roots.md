# Roots

Roots are a client capability that tells servers which filesystem locations the user has put in scope. A root is a `file://` URI plus an optional display name, typically corresponding to an open project or workspace folder.

## Listing Roots

A server that declares interest sends a `roots/list` request and receives the current set of roots. When the user opens or closes a project, the client emits `notifications/roots/list_changed`, and the server should re-request the list rather than caching stale entries.

## How Servers Should Use Roots

Roots are guidance, not enforcement: they tell a well-behaved server where the user expects it to operate. A filesystem server should scope its file listings and searches to the advertised roots, and refuse or warn on paths outside them. Actual sandboxing remains the host's responsibility, because a malicious server can simply ignore the hint.
