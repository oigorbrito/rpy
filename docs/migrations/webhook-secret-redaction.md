# Webhook secret redaction

The Judit callback URL currently authenticates with an opaque token in the path because the documented callback contract does not provide an application-controlled signature or custom authentication header.

A URL credential must not become a durable logging credential. `JuditWebhookSecretRedactionMiddleware` therefore:

- recognizes only `/webhooks/judit/<token>` requests;
- copies the original token into request-local ASGI state;
- replaces both `scope.path` and `scope.raw_path` with `/webhooks/judit/__redacted__` before FastAPI handles the request;
- lets the existing route keep the same public callback shape;
- authenticates using the request-local original value with constant-time comparison.

The redaction protects application/Uvicorn logging that derives request paths from the ASGI scope. Reverse proxies, CDNs and ingress controllers see the URL before it reaches the application and must independently redact or suppress the webhook path in access logs.

Do not include `JUDIT_WEBHOOK_TOKEN` in structured logs, exception metadata, tracing attributes, metrics labels or audit-log metadata. Token rotation still requires changing the configured callback URL and `JUDIT_WEBHOOK_TOKEN` together.
