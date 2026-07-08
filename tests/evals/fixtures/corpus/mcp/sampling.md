# Sampling

Sampling reverses the usual direction of MCP: it lets a server ask the client to run an LLM completion on its behalf. The server never needs its own API key — the host controls which model runs, what it costs, and what the user sees.

## Requesting a Completion

The server sends a `sampling/createMessage` request containing a list of messages, an optional system prompt, and sampling parameters such as `maxTokens` and `temperature`. The client routes the request to a model, then returns the assistant's reply with the model name and the stop reason.

## Model Preferences

Servers cannot name a concrete model — model availability differs per host. Instead they express preferences along three axes: `costPriority`, `speedPriority`, and `intelligencePriority`, each from 0 to 1, plus optional `hints` containing model-name substrings the host may match. The host weighs these preferences against its own configuration to pick the model.

## Human in the Loop

Hosts should keep a human in the loop for sampling. The recommended pattern is that the user can inspect and edit both the outgoing prompt and the generated completion before either is released. A server must tolerate the request being rejected by the user, which surfaces as an error response to `createMessage`.

## Use Cases

Sampling enables agentic server behavior without embedding credentials: summarizing a fetched document before returning it as a tool result, classifying records during an import, or generating a structured plan that the server then executes step by step. Keep server-initiated completions small and purposeful — every request consumes the user's tokens.
