# Transports

MCP defines two standard transports: stdio and Streamable HTTP. The transport carries JSON-RPC messages between client and server; the protocol layer above it is identical in both cases.

## stdio Transport

In the stdio transport the client launches the server as a subprocess. The server reads JSON-RPC messages from standard input and writes responses to standard output, one message per line. Standard error is reserved for logging and is never parsed as protocol traffic — a server that prints diagnostics to stdout will corrupt the message stream. stdio is the default transport for locally installed servers and requires no network configuration.

## Streamable HTTP Transport

The Streamable HTTP transport runs the server as an independent HTTP service. The client sends each JSON-RPC message as an HTTP POST to the server's MCP endpoint. The server may answer with a single JSON response or upgrade to a Server-Sent Events stream to deliver multiple messages, enabling server-initiated notifications and requests. Sessions are tracked with the `Mcp-Session-Id` header returned during initialization.

## Deprecated: HTTP+SSE Transport

The older HTTP+SSE transport from protocol version 2024-11-05 used a separate SSE endpoint alongside the POST endpoint. It is superseded by Streamable HTTP. Servers wanting backwards compatibility can host both, but new implementations should support only Streamable HTTP.

## Choosing a Transport

Use stdio when the server runs on the same machine as the host: it is simpler, faster to start, and inherits the user's local credentials and filesystem access. Use Streamable HTTP when the server is shared, remote, or long-lived — for example a team documentation server or a hosted API gateway. Authorization via OAuth applies only to HTTP transports; stdio servers rely on process-level trust.

## Custom Transports

Implementations may layer MCP over other channels (WebSockets, UNIX sockets) as long as JSON-RPC message ordering and the lifecycle semantics are preserved. Custom transports sacrifice out-of-the-box interoperability and should be reserved for closed ecosystems.
