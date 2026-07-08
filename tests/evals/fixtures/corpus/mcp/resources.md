# Resources

Resources expose data from a server to the client: file contents, database rows, API responses, or any other context the host application may want to show a user or feed to a model. Resources are application-driven — the host decides when and how to use them.

## Resource URIs

Every resource is identified by a URI such as `file:///project/src/main.py` or `postgres://db/customers`. The scheme conveys the kind of source. URIs are opaque identifiers from the client's perspective; clients must not parse meaning out of them beyond display purposes.

## Listing and Reading Resources

Clients enumerate available resources with `resources/list`, which returns each resource's URI, name, optional description, and MIME type. Reading a resource is done with `resources/read`, which returns the contents either as UTF-8 `text` or base64-encoded `blob` data, tagged with the MIME type.

## Resource Templates

Servers can advertise parameterized resources using URI templates (RFC 6570), for example `weather://forecast/{city}/{date}`. Templates appear under `resources/templates/list`. The client expands the template with concrete values before calling `resources/read`. Templates let a server expose an unbounded family of resources without listing each one.

## Subscriptions

A client may subscribe to a specific resource with `resources/subscribe`. When the underlying data changes, the server emits `notifications/resources/updated` carrying the URI, and the client re-reads the resource if it cares. Servers declare the `subscribe` capability to enable this; `unsubscribe` cancels an existing subscription.

## Choosing Between Resources and Tools

Use a resource when the data is something a user or host should browse and select — documents, configuration, logs. Use a tool when the model should decide at inference time to fetch or compute something. The same underlying data can reasonably be exposed both ways, and many servers do exactly that.
