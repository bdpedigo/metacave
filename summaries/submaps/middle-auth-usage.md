# middle_auth Usage Across the CAVE Stack

> Last investigated: 2026-04-01

## Pattern

All Flask-based CAVE services depend on the `middle_auth_client` Python library and import its decorators to protect routes. The library reads `AUTH_URI` / `AUTH_URL` / `STICKY_AUTH_URL` environment variables to know where the middle_auth server lives; these are injected by the Kubernetes deployment templates in CAVEdeployment.

Token flow: clients (CAVEclient, neuroglancer, browser) attach a `middle_auth_token` as a cookie or URL query parameter; `middle_auth_client` decorators intercept the Flask request, call middle_auth to validate the token and check permissions, and return 401/403 before the route handler runs.

---

## Services (use `middle_auth_client` decorators)

| Service | Dependency declaration | Decorators used |
|---|---|---|
| **AnnotationEngine** | `requirements.in`: `middle-auth-client>=3.11.1` | `auth_requires_permission`, `auth_required`, `auth_requires_admin` |
| **AnnotationFrameworkInfoService** | `requirements.in`: `middle-auth-client>=3.16.0` | `auth_requires_permission`, `auth_required`, `auth_requires_admin` |
| **MaterializationEngine** | `pyproject.toml`: `middle-auth-client>=3.19.0` | `auth_required`, `auth_requires_permission`, `auth_requires_admin`, `auth_requires_dataset_admin`, `users_share_common_group` |
| **NeuroglancerJsonServer** | `requirements.txt`: `middle-auth-client==3.6.4` | `auth_required`, `auth_requires_admin` |
| **PyChunkedGraph** | `requirements.in`: `middle-auth-client>=3.11.0` | `auth_requires_permission`, `auth_required` (also strips `middle_auth_token` from query args before processing) |
| **PCGL2Cache** | `requirements.txt`: `middle-auth-client>=3.11.0` | `auth_required`, `auth_requires_permission` |
| **SkeletonService** | `requirements.in`: `middle-auth-client>=3.16.0` | `auth_required`, `auth_requires_permission` |
| **Tourguide** | `pyproject.toml`: `middle-auth-client>=3.18.1` | `auth_required`; also reads `middle_auth_token` from URL query params in config |
| **dash_on_flask** | `requirements.in`: `middle-auth-client>=3.11.1` | `auth_requires_permission` |

**middle_auth** itself also depends on `middle_auth_client` to protect its own admin UI routes.

---

## Libraries / Clients (non-service usage)

| Component | Nature of usage |
|---|---|
| **CAVEclient** | Client-side only: injects user token as a `middle_auth_token` cookie on outgoing HTTP requests. Does not import `middle_auth_client`. |
| **EMAnnotationSchemas** | References `middle_auth_token` as an apiKey name in its Swagger config and reads `MIDDLE_AUTH_TOKEN` in test fixtures. No direct dependency on `middle_auth_client`. |
| **middle_auth_client** | Is the library itself. Reads `AUTH_URI`, `AUTH_URL`, `STICKY_AUTH_URL`, `TOKEN_NAME`, `MIDDLE_AUTH_MY_PERMISSION_URL` from env. |

---

## Deployment (CAVEdeployment)

`CAVEdeployment/kubetemplates/` injects `AUTH_URI`, `AUTH_URL`, and `STICKY_AUTH_URL` into every service that uses `middle_auth_client`. Relevant templates include: `pychunkedgraph.yml`, `materialize.yml`, `materialize_worker.yml`, `pcgl2cache.yml`, `meshing.yml`, `info.yml`, `nglstate.yml`, `guidebook.yml`, `tourguide.yml`, `dash.yml`, `pmanagement.yml`, `pprogress.yml`, `skeletoncache_integrationtests_only.yml`. The `auth.yml` and `sticky_auth.yml` templates deploy the middle_auth server and sticky-auth proxy themselves.
