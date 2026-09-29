# GitHub job website access

## Result and boundaries

Replace the finite company-domain manifest list with HTTP/HTTPS host permissions
in extension 0.8.3. Keep explicit target dispatch, runtime permission checks,
bounded worker tabs, local-only backend CSP, and the existing narrowly scoped
LinkedIn/Handshake request-header listeners. No new hash logic or database edits.
Existing permission failures are already retried on the next scan; completed
bodies remain reused. Website availability and extraction failures remain errors.

## Verification

- Before change: manifest-backed worker regression failed for unseen company
  targets and the Red Ventures redirect (15 passed, 1 failed).
- After change: all 91 extension tests passed.
- All 12 incremental source tests passed, including permission retry without
  native refetch and reuse of complete stored descriptions.
- JavaScript syntax and scoped diff whitespace checks passed.

## Activation remaining

The source change is ready locally. Chrome activation expands access to all
HTTP/HTTPS websites, so obtain the required action-time confirmation before
loading the changed extension. Then activate on Mini and verify a targeted Red
Ventures retry using the actual extension and persisted JD result. No live
extraction success has been claimed yet.
