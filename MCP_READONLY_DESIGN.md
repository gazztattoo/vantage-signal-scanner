# Global Signal Scanner — Read-only MCP integration

## Status
Design-only checkpoint. This document does not add a server, credentials, routes,
database policies, Render services, or deployment configuration.

Target branch: `mcp-readonly-test`
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

### Recommended two-token boundary (design only; not implemented)
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

### TEST API authorization behavior required
- Preserve the existing panel-token path for the panel; do not weaken it.
- Add a distinct read-only JWT verification path only for the exact allowlisted
  GET data routes.
- Require the expected Auth0 issuer, exact TEST API audience, RS256 signature,
  unexpired token and `scanner:read` scope.
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
