# Model Context Protocol Overview

The Model Context Protocol (MCP) is an open protocol that standardizes how applications provide context to large language models. MCP follows a client-server architecture where a host application creates and manages multiple clients, and each client maintains a one-to-one connection with a server.

## Architecture

MCP separates concerns across three roles. The host is the LLM application (such as Claude Desktop or an IDE) that initiates connections and orchestrates the overall session. The client lives inside the host and maintains a stateful session with exactly one server. The server is a separate process or service that exposes capabilities — tools, resources, and prompts — over the protocol.

## Protocol Layers

MCP is built on JSON-RPC 2.0 message framing. Every message is a request, a response, or a notification. Requests carry an `id` and expect a response; notifications are fire-and-forget and must not receive a response. The protocol layer on top of JSON-RPC handles capability negotiation, lifecycle management, and the feature-specific message types.

## Capability Negotiation

During initialization the client and server exchange capability declarations. A server declares which features it supports — for example `tools`, `resources`, `prompts`, or `logging` — and the client declares client-side capabilities such as `sampling` and `roots`. Neither side may use a feature the other did not declare. This makes the protocol forward-compatible: new capabilities can be added without breaking older implementations.

## Connection Lifecycle

A session begins with an `initialize` request from the client carrying the protocol version and client capabilities. The server responds with its own capabilities and server info. The client then sends an `initialized` notification, after which normal operation begins. Either side may terminate the connection at any time; stdio-based servers exit when their stdin closes.

## Protocol Versioning

Protocol versions are date-based strings such as `2025-06-18`. The client sends the latest version it supports during initialization; if the server does not support that version, it responds with the most recent version it does support, and the client decides whether to continue or disconnect.
