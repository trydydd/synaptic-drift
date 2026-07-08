# Prompts

Prompts are reusable message templates that servers offer to clients. They are user-controlled: the host surfaces them as slash commands, menu entries, or buttons, and the user explicitly chooses to apply one.

## Prompt Definitions

A prompt has a `name`, an optional `description`, and a list of typed `arguments`. Each argument declares a `name`, a `description`, and whether it is `required`. Argument values are always strings; the server is responsible for parsing them into whatever it needs.

## Getting a Prompt

The client calls `prompts/get` with the prompt name and an `arguments` map. The server returns a list of fully rendered messages, each with a `role` (`user` or `assistant`) and a `content` block. The host then injects those messages into the model conversation. Because rendering happens server-side, the template itself never leaves the server.

## Listing Prompts

`prompts/list` returns the available prompt definitions with pagination support. Servers whose prompt set changes at runtime declare `listChanged` and emit `notifications/prompts/list_changed`, mirroring the pattern used by tools and resources.

## Embedding Resources in Prompts

Prompt messages can embed resource content directly by using an `embeddedResource` content block referencing a URI. This lets a prompt bundle relevant context — a log file, a schema, a style guide — together with the instruction text in a single user gesture.
