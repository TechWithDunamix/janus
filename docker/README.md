# `caddy-bootstrap.json`

The configuration Caddy starts with, and all of it: an open admin API and an
empty server map. Janus writes the server it owns on its first deployment.

Two things it deliberately does not do:

- **It configures no routes.** Anything defined here would be a second source
  of truth, and Janus would report it as drift on the next poll.
- **It carries no comments.** Caddy's JSON loader rejects unknown fields at the
  config root, including a `"//"` key — `loading initial config: json: unknown
  field "//"`. That is why this explanation is in a README instead.

In production, bind the admin API to a private interface or leave it on
loopback and reach it over the container network. It is a full-control
endpoint: anyone who can reach it can rewrite your edge.
