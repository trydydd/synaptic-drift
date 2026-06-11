# Tools

Tools let a server expose executable functionality to language models. Unlike resources, which are application-driven, tools are model-controlled: the model decides when to invoke them based on the task at hand.

## Defining a Tool

Each tool has a unique `name`, a human-readable `description`, and an `inputSchema` expressed as JSON Schema. The description is what the model reads when deciding whether to call the tool, so it should state what the tool does, when to use it, and what it returns. A tool may also declare an `outputSchema` describing the shape of its structured results.

## Input Schemas

The `inputSchema` must be a JSON Schema object with `type: "object"`. Each property documents one parameter; the `required` array lists mandatory parameters. Keep parameter names self-documenting — models select arguments from the schema alone. Avoid deeply nested objects: flat parameter lists are called correctly far more often.

## Listing Tools

Clients discover tools with the `tools/list` request. The response contains an array of tool definitions and supports pagination through an opaque `nextCursor`. Servers that change their tool set at runtime should declare the `listChanged` capability and emit a `notifications/tools/list_changed` notification so clients can re-fetch.

## Calling Tools

A tool is invoked with `tools/call`, passing the tool `name` and an `arguments` object that must satisfy the input schema. The result contains a `content` array of content blocks — text, images, or embedded resources — and an optional `structuredContent` field when the tool declared an output schema.

## Error Handling

Tool execution errors are reported inside the result with `isError: true`, not as JSON-RPC protocol errors. This distinction matters: a protocol error means the request itself was malformed, while an execution error is a legitimate result the model should see and can react to, such as an upstream API returning a failure.

## Tool Annotations

Annotations give clients hints about tool behavior: `readOnlyHint` marks tools that do not modify state, `destructiveHint` marks tools that may perform irreversible changes, and `idempotentHint` marks tools safe to retry. Annotations are advisory metadata — clients must not treat them as security guarantees because servers are not trusted to report them honestly.
