# Logging

The logging capability lets a server stream structured log messages to the client, where they can be shown in a debug console or written to the host's log files. Servers declare the `logging` capability during initialization to enable it.

## Log Levels

MCP adopts the eight syslog severities: `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, and `emergency`. The client sets the minimum severity it wants with a `logging/setLevel` request; the server then suppresses anything below that level.

## Emitting Messages

The server emits `notifications/message` notifications carrying the `level`, an optional `logger` name, and arbitrary JSON `data`. Because these are notifications, they never block protocol traffic and the client never replies. Structured data is preferred over preformatted strings so hosts can filter and render messages meaningfully.

## stdio Servers and stderr

For stdio-transport servers there is a second, cruder channel: anything written to standard error is captured by the host and typically appended to its own logs. Use stderr for early-startup diagnostics that occur before the protocol session exists, and the logging capability for everything after initialization. Never write logs to stdout on a stdio server — stdout is the protocol channel.
