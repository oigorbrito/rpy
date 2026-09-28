# External connectivity diagnostic

Temporary diagnostic branch used to distinguish generic Rpy outbound HTTPS failures from provider-specific failures.

Run from the same Python environment/container that performs provider calls:

```bash
python scripts/external_connectivity_smoke.py
```

The probe performs one credential-free HTTPS `POST` to Postman Echo using `urllib.request`, sends only synthetic JSON plus a synthetic marker header, and verifies that JSON, the marker header, and content type make a round trip.

Interpretation:

- `status=ok`: outbound DNS/TLS/HTTPS, POST, request headers, JSON request body, response receipt, bounded reading, and JSON parsing all worked as a single black-box transaction.
- `transport_error` or `dns_or_transport_error`: investigate the local/container network, proxy, DNS or TLS path before changing provider payloads.
- `http_*`: the remote service was reached and returned an HTTP rejection.
- `echo_contract_mismatch`: the remote service answered but the request/response contract did not round-trip as expected.

This diagnostic does not alter the production egress allowlist and is not intended to become a product dependency.
