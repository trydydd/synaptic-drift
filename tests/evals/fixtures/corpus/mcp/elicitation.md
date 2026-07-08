# Elicitation

Elicitation lets a server request structured input from the user mid-operation. Where sampling asks the client's model for a completion, elicitation asks the client's human for data — confirmation, a missing parameter, a choice among options.

## Requesting Input

The server sends an `elicitation/create` request with a human-readable `message` explaining what is needed and a `requestedSchema` describing the expected response shape. The schema is a flat JSON Schema object limited to primitive properties — strings, numbers, booleans, and enums — so hosts can render it as a simple form.

## Response Actions

The client's response carries one of three actions. `accept` means the user provided the data, which arrives in the `content` field conforming to the requested schema. `decline` means the user explicitly refused. `cancel` means the user dismissed the request without deciding. Servers must handle all three: treat `decline` as a definitive no, and `cancel` as "ask again later or abort gracefully".

## Design Guidance

Elicitation interrupts the user, so reserve it for information the server genuinely cannot proceed without. Never use elicitation to collect secrets — passwords, API keys, or tokens — because the values transit the protocol in plain text and may be logged by either side. Hosts are expected to make the requesting server's identity visible so users know who is asking.
