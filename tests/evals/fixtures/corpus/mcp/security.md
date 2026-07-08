# Security Considerations

MCP connects hosts to servers written by third parties, so every integration is a trust decision. The protocol provides primitives — capability declarations, authorization, annotations — but enforcement always lives in the host.

## Confused Deputy Problems

A server that holds privileged credentials can be tricked into using them on behalf of an attacker who controls part of the input — for example, a prompt injection embedded in a fetched web page instructing a tool to exfiltrate data. Servers should treat all retrieved content as untrusted data, never as instructions, and hosts should show users what a tool is about to do with state-changing operations.

## Token Passthrough

An MCP server must never forward the access token it received to upstream APIs, and must reject tokens whose audience is some other service. Accepting passthrough tokens collapses the security boundary between services and makes audit trails meaningless. Each hop should hold its own credential with its own scope.

## User Consent

Hosts are expected to obtain explicit user consent before invoking tools with side effects, before sampling requests consume the user's model budget, and before granting a server access to new roots. Consent fatigue is a real risk: group related permissions and remember decisions where safe, but never silently escalate.

## Supply Chain Hygiene

Treat MCP servers like any other dependency: pin versions, review what a server can reach, and prefer servers that run with the least privilege their function allows. A documentation search server has no business holding filesystem write access or outbound network reach beyond its index.
