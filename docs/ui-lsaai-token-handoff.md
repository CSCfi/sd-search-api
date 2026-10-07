# sd-search-ui: LS AAI session refresh and authenticated Dataset-on-Demand calls

These are instructions for an agent implementing the UI side of the sd-search-api change
`replace-session-jwt-with-lsaai-token`. Work only in the sd-search-ui repository. This document is
self-contained. The API's own plan is kept outside git, so do not look for it.

## Background

Until now, sd-search-api created its own 7-day session token after the LS AAI login. After this
change, the session is the user's **LS AAI access token**, which is valid for only **1 hour**. A
longer-lived **refresh token** (about a month) renews it. The UI needs to do two things:

1. Keep users logged in past the hour by calling `POST /refresh` when a request comes back `401`.
2. Send Dataset-on-Demand (DoD) requests with the user's LS AAI access token as a bearer token,
   in place of the current unauthenticated calls that send `user: 'placeholder'`.

Rule that must not be broken (`.claude/rules/auth.md`): **no token ever reaches JavaScript.** Do
not add `oidc-client-ts`. Do not read `document.cookie`. Do not put tokens in storage. The bearer
header for DoD is added by nginx, not by the browser.

## What the API now provides

Every one of these must be requested through the UI's own origin, never the API's hostname.
The cookies are host-only.

| | Behaviour |
|---|---|
| `access_token` cookie | The LS AAI access token. `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age` 1 hour. Set by `/callback` and `/refresh`. |
| `refresh_token` cookie | The LS AAI refresh token. `HttpOnly`, `SameSite=Strict`, `Path=/refresh`, so the browser sends it only to `/refresh`. |
| `POST /refresh` | Needs no request body; it reads the refresh cookie. **`204`**: both cookies replaced. **`401`**: the session is over and the user must log in. That covers no refresh cookie, or LS AAI rejecting it, in which case both cookies are also cleared. **`503`**: LS AAI unreachable; cookies unchanged, so this is not a logout. |
| Any protected API path | **`401`** when the access token is missing, invalid or expired. **`503`** when the API cannot fetch LS AAI's signing keys. |
| `/login`, `/callback`, `/logout` | Unchanged. `/logout` now clears both cookies. |

Each refresh replaces the refresh token, and the old one stops working. If two refreshes run in
parallel, the second one presents a spent token, gets `401`, and logs the user out. **So the UI
must never run two refreshes at once** (see change 3).

## Changes

### 1. nginx: forward `/refresh` (`docker/nginx.conf`)

Add a `location = /refresh` next to `/login`, `/callback` and `/logout`. Make it the same kind of
exact-match location, `proxy_pass ${BACKEND_URL}/refresh`, with the same `Host` and `X-Real-IP`
headers. The browser must send the request to `/refresh` on the UI's own origin, or the
path-scoped refresh cookie is not sent.

### 2. nginx: proxy DoD and add the bearer token (`docker/nginx.conf`, entrypoint, Dockerfile)

- **Read the DoD URL when the container starts.** Add a runtime environment variable `DOD_URL`.
  It is the same URL that `VITE_DOD_ENDPOINT_URL` holds today: the submission endpoint, with the
  status endpoint at `<DOD_URL>/<accession>/status`. Validate it in
  `docker/docker-entrypoint-validate.sh` the way `BACKEND_URL` is validated. Decide whether it may
  have a path, and write the nginx location to match.
- **Proxy these two routes:**
  - `POST /dod` goes to `POST ${DOD_URL}`
  - `GET /dod/<accession>/status` goes to `GET ${DOD_URL}/<accession>/status`
- **Set these headers in that location:**
  ```nginx
  proxy_set_header Authorization "Bearer $cookie_access_token";
  proxy_set_header Cookie "";          # never forward our cookies to a third party
  proxy_set_header Host $proxy_host;
  proxy_ssl_server_name on;            # DoD is HTTPS on another host; SNI is needed
  ```
- **Watch the URI rewriting.** A `proxy_pass` with a URI behaves differently in prefix locations,
  regex locations, and with `rewrite ... break`; the existing `/api/` block has a comment about
  this. Check the forwarded URI with a test upstream (change 6).
- **Remove `VITE_DOD_ENDPOINT_URL` everywhere it appears:**
  - the `ARG` in `docker/Dockerfile`
  - `.env.example`
  - the CI build arguments in `.github/workflows/ci.yml`
  - `README.md`

  Document `DOD_URL` in its place.

### 3. One refresh at a time, then retry (`src/services/apiClient.ts`)

Replace the `401` handling in the response interceptor. Today it sends the browser to `/logout`.

- **On a `401` from any request** (other than `/refresh` itself, and other than a request that
  has already been retried once):
  1. Call `POST /refresh` with `withCredentials: true`, using plain axios. Do not use `apiClient`,
     whose `baseURL` is `/api`; `/refresh` is at the root of the origin.
  2. Share a single in-flight refresh promise. Every `401` that arrives while a refresh is running
     waits for that same promise; do not start another refresh.
  3. If the refresh returns `204`, retry the original request once.
  4. If it returns `401`, do what a `401` does today: clear `postLoginRedirect` and set
     `window.location.href = '/logout'`.
  5. If it returns `503` or a network error, reject with an `ApiError` (status `503`) and do
     **not** log the user out.
- **Put the refresh logic in its own module**, for example `src/services/sessionRefresh.ts`. The
  DoD calls (change 4) and the session check (change 5) need the same logic.

### 4. DoD calls go through nginx (`src/services/api.ts`, `useDatasetOnDemand.ts`)

- **`submitDatasetOnDemand`:**
  - `POST /dod`, with `withCredentials: true`, so the `access_token` cookie is sent and nginx can
    turn it into the bearer header
  - drop `user: 'placeholder'` and its `TODO(auth)` comment; DoD identifies the user from the
    token
  - keep the request body and the handling of `202` versus other success responses
- **`pollDatasetOnDemandStatus`:** `GET /dod/<accession>/status`, with `withCredentials: true`.
- **Expired tokens:** both calls refresh and retry once when they get a `401`, using the shared
  module from change 3. Polling runs for up to a minute and can cross the moment the access token
  expires.
- **Comments:** update the comments in `api.ts` that say these calls skip cookies on purpose.

### 5. The session check must try refreshing first (`src/stores/authStore.ts`)

`checkSession()` currently calls `GET /api/filtering_terms`, and any error means "not logged in".
After an hour, the access cookie has expired but the refresh cookie still works. Without a change,
someone returning to the app would look logged out.

- On a `401`, call the shared refresh once and repeat the check.
- `isLoggedIn` becomes `false` only if the refresh also fails with `401`.
- On a `503` from the refresh, do not mark the user as logged out. Leave `isLoggedIn` as it is, or
  surface an error.

Fix the stale comment at `authStore.ts:13-14`. It says the guard redirects to `/login`, but the
guard redirects to `/`.

### 6. Tests and docs

- **Unit tests (Vitest)** in the existing test files (`api.test.ts`, `useDatasetOnDemand.test.ts`,
  and the store and router tests):
  - a `401` followed by a successful refresh retries the original request once
  - parallel `401`s trigger exactly one `POST /refresh`
  - a refresh `401` redirects to `/logout`
  - a refresh `503` does not redirect
  - a retried request that gets `401` again does not loop
  - the DoD calls hit `/dod...` with `withCredentials: true`, and their body has no `user` field
  - `checkSession()` refreshes before reporting logged out
- **nginx check:** with `docker compose`, point `DOD_URL` at a stub that echoes its request, for
  example a tiny echo server. Confirm that it receives `Authorization: Bearer <cookie value>`, no
  `Cookie` header, and the right path for both routes. Confirm that `POST /refresh` reaches the
  backend.
- **`vite.config.ts`:** add `/refresh` to the dev proxy beside `/login`, `/callback` and `/logout`.
  For `/dod`, the Vite dev proxy cannot read the cookie and add a header without custom code. Use
  the Docker image to test DoD, and note this in the README.
- **`.claude/rules/auth.md`:** describe the refresh flow, the rule that only one refresh runs at a
  time, and that DoD auth is added by nginx from the cookie. Remove "token refresh" from "What Is
  Not Implemented Yet".

## Out of scope

- Any change to the backend API (sd-search-api) or to `k8s/` manifests; those live in the API
  repository.
- A Content Security Policy. Making DoD same-origin makes one possible, but leave it for a
  separate change.
- Keeping two tabs from refreshing at the same moment. This is rare, and the losing tab only goes
  through `/login` again.

## Open question that does not block this work

The DoD/SDA team has not yet confirmed that they accept an LS AAI access token whose `aud` is the
sd-search-api client. If they don't, the API will later take over the `/dod` proxy. The UI-facing
paths `/dod` and `/dod/<accession>/status` would stay the same, so build against them as described
here.
