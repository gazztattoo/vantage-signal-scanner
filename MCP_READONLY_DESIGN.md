# Global Signal Scanner — Read-only MCP integration

## Status
Read-only API authorization draft prepared on branch `mcp-readonly-auth-test`.
The changes are not deployed and have not been run against the TEST service.
Auth0 resource/M2M configuration and private environment variables are still
pending. Review the code and test diff before any deployment.

Target branch: `mcp-readonly-auth-test`
Design parent branch: `mcp-readonly-test`
Baseline: `8019244f244b06c3b5153a38aa56763899f4953a`

## Non-negotiable boundaries
- Never modify `main` or production services as part of this integration.
- Do not deploy or push to `push-test`; Render auto-deploys that branch.
- Do not expose source code, API keys, tokens, or environment-variable values.
- No SQL, arbitrary HTTP, shell, filesystem, or generic proxy tools.
- No write endpoints, signal scans, push subscriptions, push tests, or notifications.
- MCP tools must be read-only and restricted to the TEST environment.
- Show diffs and request approval before any deployment or promotion.

## Intended MCP tool allowlist
1. `get_panel_health` — GET `/api/health`
2. `get_market_status` — GET `/api/market-status`
3. `get_signal_analysis` — GET `/api/analysis` with validated symbol/interval
4. `get_signal_history` — GET `/api/history` with bounded limit and allowlisted filters
5. `get_signal_performance` — GET `/api/performance`
6. `get_lab_status` — GET `/api/lab-status`

No tool may call POST, PUT, PATCH, or DELETE routes.

## Authentication design requirements
- Use Auth0 as the OAuth 2.1 authorization server / identity provider.
- Validate access-token signature (RS256), issuer, audience, expiry, and required scope.
- Require a dedicated read-only scope such as `scanner:read`.
- Do not treat an Auth0 login token as an existing panel API token.
- Do not embed Auth0 client secrets, signing keys, or API credentials in source.
- Do not reuse the panel's broad `PANEL_ACCESS_CODE` or `ACCESS_TOKEN_SECRET`
  to mint general panel tokens for the MCP service.
- Do not use the Auth0 Regular Web application already created as an assumed
  machine-to-machine credential. Its role and client-registration compatibility
  must be confirmed separately.
- Confirm ChatGPT's supported OAuth client-registration mode before configuring
  Auth0 callbacks or creating a production-facing authorization client.

## Upstream data-access blocker
The current protected TEST API endpoints use the panel's general bearer token.
That token is not least-privilege: it may authorize routes beyond GET-only reads.
The MCP service must not obtain or mint that token.

### Recommended two-token boundary (MCP leg still design-only)
Use two independently validated authorization legs:
1. **ChatGPT → MCP:** ChatGPT presents an access token whose audience is the MCP
   resource server. MCP validates its signature, issuer, audience, expiry and
   required MCP scope before accepting a tool call.
2. **MCP → TEST API:** MCP obtains a separate Auth0 machine-to-machine access
   token using the client-credentials grant. Its audience is a dedicated TEST
   API resource identifier and it carries only `scanner:read`. The TEST API
   validates that token independently.

This avoids forwarding ChatGPT's MCP token to the upstream API and avoids using
the panel's general-purpose token. The M2M client secret, if this design is
approved, belongs only in the MCP service's private environment configuration;
never in GitHub, tool arguments, logs, or chat. The Auth0 M2M application and
TEST API audience/scope still need to be created and configured deliberately.

### TEST API authorization behavior implemented in the draft branch
- Preserve the existing panel-token path for the panel; do not weaken it.
- Draft adds a distinct read-only JWT verification path only for the exact
  allowlisted GET data routes in `app.py`, backed by `readonly_auth.py`.
- Verifier requires the configured Auth0 issuer, exact TEST API audience,
  RS256 signature, expiry/issued-at claims and `scanner:read` scope.
- Reject the read-only token on every POST/PUT/PATCH/DELETE route, including
  `/api/access`, all `/api/push/*` routes and `/api/push/scan`.
- Reject query parameters outside each tool's explicit schema and bounds.
- Do not let the read-only credential bypass existing authorization on any
  other route. No fallback to the panel token and no automatic token minting
  through `/api/access`.

### Alternative
A separate read-only data facade may be considered if it can access upstream
data without the panel token and without a Supabase service-role key. It must
still expose only explicit read operations and enforce bounded inputs.

The Supabase service-role key must never be placed in the MCP service.

## Required verification before deployment
- Unit tests proving every exposed tool only calls its exact allowlisted GET route.
- API tests proving the read-only token succeeds only on approved GET routes and
  is rejected on all push, access, and mutation routes.
- JWT tests rejecting wrong issuer/audience, bad signature/algorithm, missing
  scope, expired tokens and malformed claims.
- Input tests rejecting unknown symbols, intervals, filters and oversized limits.
- Static scan confirming no secrets are committed or logged.
- Integration test against TEST only; no production URL or credentials.
- Review the complete diff and deployment plan with the user.
- Keep the MCP service separate from the existing panel and API services.
- Do not deploy until the user explicitly approves the reviewed diff and plan.

## OAuth role clarification (verified against MCP SDK v2 docs)
- Auth0 is the external authorization server / identity provider.
- The MCP service is an OAuth resource server. It validates bearer access tokens;
  it must not issue tokens or run a custom login/token endpoint.
- The MCP service must publish Protected Resource Metadata (RFC 9728) and
  advertise its Auth0 issuer and the required MCP scope.
- The Auth0 application created in the dashboard is not yet configured:
  callback/logout/origin fields must remain blank until the public MCP URL and
  the exact OAuth client-registration approach are confirmed.
- Do not reveal or copy the Auth0 Client Secret into chat or commit it to GitHub.
- Before completing ChatGPT OAuth setup, verify that the selected Auth0 tenant
  supports the client-registration method ChatGPT expects (Dynamic Client
  Registration or Client ID Metadata Documents). Do not assume the Regular Web
  application by itself completes MCP OAuth.

### Draft implementation details
- New environment settings expected by the API: `AUTH0_READONLY_ISSUER`,
  `AUTH0_READONLY_AUDIENCE`, and `AUTH0_READONLY_SCOPE=scanner:read`.
- New dependency: `PyJWT[crypto]` (major version 2).
- The existing panel signed-token path remains available for the panel.
- The read-only credential is checked only for GET on the five existing private
  data routes. Push endpoints remain panel-token-only.
- Added mocked unit tests under `tests/`. They are authored but have NOT been
  executed in this environment; a passing result must not be assumed.
- Query-parameter schema hardening is not added to the Flask API draft yet;
  MCP tool wrappers must validate arguments before making upstream requests.

## Current verified API boundary
- `/api/health` and `/api/market-status` are public GET endpoints.
- `/api/analysis`, `/api/price`, `/api/history`, `/api/performance`,
  and `/api/lab-status` are behind the same signed panel bearer token.
- The current API access middleware also protects push subscribe/test/unsubscribe
  routes with that same token. Therefore the token is broader than read-only.
- The MCP service must not call `/api/access`, because that would require the
  panel access code and mint the broad panel token.
- No MCP implementation should be connected to the protected routes until a
  separate read-only credential/authorization boundary is implemented and tested
  in TEST, with no access to push or mutation routes.


## Proposed implementation plan — review checkpoint (no application code changed)

### What was inspected
- `app.py` defines a Flask `before_request` guard that currently protects
  analysis, price, history, performance, lab-status and three push POST routes
  with one itsdangerous panel token.
- `/api/access` issues that broad panel token after checking the panel access code.
- History/performance read data through the existing API's Supabase REST helper.
- `requirements.txt` currently has no JWT/JWKS verification library.
- This inspection was read-only. No application source, dependencies, tests,
  environment variables, database policies, or deployments were changed.

### Proposed TEST-only code change (not yet authorized or implemented)
1. Add a dedicated Auth0 API resource for the TEST API with a unique audience
   identifier and a single permission `scanner:read`.
2. Add a separate M2M client for the MCP service. Its client-credentials grant
   requests a token for that TEST API audience with only `scanner:read`.
   Store its client secret only in the future MCP service's private environment;
   it must never be placed in GitHub or this API source.
3. Add a small JWT verifier to the TEST Flask API, using Auth0's published JWKS
   and a maintained JWT library (for example PyJWT with cryptographic support).
   Pin the dependency after compatibility review. Cache JWKS safely; validate
   RS256, exact issuer, exact TEST API audience, expiry and `scanner:read`.
   Fail closed on missing configuration, invalid token or JWKS/network failure.
4. Keep the current panel-token validation intact. On the protected data GET
   routes only, accept either the existing panel token OR a valid dedicated
   read-only API token. The read-only token must never satisfy panel access,
   push routes or any non-GET route. Do not alter `/api/access`.
5. Keep the allowlist explicit: `GET /api/price`, `GET /api/analysis`,
   `GET /api/history`, `GET /api/performance`, `GET /api/lab-status`.
   Health and market-status remain public as they are today. MCP tools should
   still expose only the six named read tools and validate all arguments.
6. Preserve the panel's existing behavior and response payloads. Do not change
   signal calculation, signal history, Supabase policies/data, push code,
   notification delivery, UI, banner, or spacing.

### Mandatory test matrix before any deployment
- Existing panel token: current protected panel GETs still work as before.
- Valid Auth0 read-only token: succeeds on each explicitly approved data GET.
- Same read-only token: rejected on `POST /api/access`, all `/api/push/*`
  routes (including public-key if policy keeps it public, no write access either
  way), and every other non-allowlisted route.
- Wrong issuer, audience, algorithm, signature, expired token, missing scope,
  malformed claims and unavailable JWKS: rejected safely.
- Unknown symbols/intervals/filters, repeated or unknown query keys and
  out-of-range limits: rejected or constrained according to documented schema.
- OPTIONS/CORS behavior and existing panel browser flow remain unchanged.
- Unit tests use mocked JWKS and mocked upstream calls; integration tests point
  only at TEST. No live notification, scan, database write or trading action.
- Secret scan confirms no Auth0 credentials or runtime secrets in Git history,
  source, fixtures, logs or test output.

### Scope and deployment gate
This change is limited to a proposed diff on `mcp-readonly-test`. Do not push
`push-test`, because it auto-deploys. Do not create or modify a Render service,
Auth0 callback, production resource, or deployment until the code diff, test
results, TEST environment-variable names/values handling, and rollback plan have
been shown to the user and explicitly approved.
