# Authorization

MCP authorization is defined for HTTP-based transports and is built on OAuth 2.1. It lets a server require a valid access token before serving protocol requests, and lets clients obtain that token through a standard flow without shipping per-server credential logic.

## When Authorization Applies

Authorization is optional. stdio servers do not use OAuth — they run with the launching user's privileges and obtain credentials from the environment. An HTTP server that requires authorization responds to unauthenticated requests with HTTP 401 and a `WWW-Authenticate` header pointing at its protected resource metadata.

## Discovery

Clients discover the authorization server through protected resource metadata (RFC 9728) served at a well-known URI. The metadata names one or more authorization servers; the client then fetches the authorization server's own metadata (RFC 8414) to find the authorization, token, and registration endpoints.

## Authorization Code Flow with PKCE

Clients act as OAuth public clients and must use the authorization code grant with PKCE. The client generates a code verifier, opens the user's browser at the authorization endpoint with the code challenge, receives the authorization code at a localhost redirect URI, and exchanges the code plus verifier for an access token at the token endpoint. Refresh tokens, when issued, let the client renew access without user interaction.

## Dynamic Client Registration

Because users connect to servers the client author has never seen, clients should support dynamic client registration (RFC 7591). The client registers itself with the authorization server at first use and receives a client ID, avoiding any manual credential exchange.

## Using Access Tokens

The access token is sent on every HTTP request in the `Authorization: Bearer` header. Tokens must be bound to the intended server through the resource indicator (RFC 8707) so a token issued for one MCP server cannot be replayed against another. Servers must validate the token's audience, expiry, and signature on every request.

## Scopes and Least Privilege

Authorization servers may partition access with OAuth scopes — for example separating read-only tool invocation from state-changing operations. Clients should request the narrowest scope set that satisfies the user's intent and surface scope upgrades to the user explicitly.
