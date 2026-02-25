# Security

This document describes the security measures, hardening, and vulnerability
mitigations applied to HaqSetu.

---

## Table of Contents

- [Dependency Management](#dependency-management)
- [CI/CD Security Pipelines](#cicd-security-pipelines)
- [Key Generation & Rotation](#key-generation--rotation)
- [Authentication & Authorisation](#authentication--authorisation)
- [CORS Policy](#cors-policy)
- [Content Security Policy (CSP)](#content-security-policy-csp)
- [Security Headers](#security-headers)
- [Rate Limiting & IP Spoofing Protection](#rate-limiting--ip-spoofing-protection)
- [XSS Prevention](#xss-prevention)
- [SSRF Protection](#ssrf-protection)
- [Upload & DoS Protection](#upload--dos-protection)
- [Container Hardening](#container-hardening)
- [Redis Hardening](#redis-hardening)
- [Infrastructure (Terraform)](#infrastructure-terraform)
- [Deployment Safety](#deployment-safety)
- [Error Handling](#error-handling)
- [DPDPA Compliance & Privacy](#dpdpa-compliance--privacy)
- [CDN & External Domain Whitelist](#cdn--external-domain-whitelist)
- [Reporting a Vulnerability](#reporting-a-vulnerability)

---

## Dependency Management

**File:** `.github/dependabot.yml`

Dependabot is configured to automatically create pull requests for dependency
updates and security patches across three ecosystems:

| Ecosystem       | Schedule        | Grouping                                                 |
| --------------- | --------------- | -------------------------------------------------------- |
| **pip**         | Weekly (Monday) | google-cloud, fastapi-ecosystem, testing, security-tools |
| **Docker**      | Weekly (Tuesday)| --                                                       |
| **GitHub Actions** | Weekly (Wednesday) | --                                                  |

- Security updates are always raised immediately regardless of schedule.
- Groups minor/patch updates together to reduce PR noise.

---

## CI/CD Security Pipelines

### CI Pipeline (`.github/workflows/ci.yml`)

| Job               | Tool       | Purpose                                  |
| ----------------- | ---------- | ---------------------------------------- |
| Lint & Format     | Ruff       | Code quality and style enforcement       |
| Test Suite        | pytest     | 550+ tests with coverage (>50% threshold)|
| Security Lint     | Bandit     | Python SAST for common vulnerabilities   |
| Dependency Audit  | pip-audit  | Known CVE detection in dependencies      |
| Docker Build Test | Docker     | Verifies non-root user and health check  |

### Security Pipeline (`.github/workflows/security.yml`)

| Job               | Tool              | Purpose                                |
| ----------------- | ----------------- | -------------------------------------- |
| Secret Detection  | TruffleHog        | Scans commit history for leaked secrets|
| Container Scan    | Trivy             | Scans Docker image for CRITICAL/HIGH CVEs |
| CodeQL Analysis   | GitHub CodeQL     | Semantic code analysis for Python      |
| Dependency Review | dependency-review | Blocks PRs introducing high-severity deps or GPL-3.0/AGPL-3.0 licenses |

Runs on every push to main, every pull request, and weekly on schedule.

---

## Key Generation & Rotation

**File:** `scripts/generate_keys.sh`

All cryptographic keys are generated using CSPRNG via `openssl rand`:

| Key                       | Spec                  | Purpose                       |
| ------------------------- | --------------------- | ----------------------------- |
| `ENCRYPTION_KEY`          | 256-bit, base64       | AES-256 data encryption       |
| `REDIS_PASSWORD`          | 64-char, alphanumeric | Redis authentication          |
| `HAQSETU_ADMIN_API_KEY`   | 64-char, hex          | Admin endpoint authentication |
| `HAQSETU_SESSION_SECRET`  | 128-char, hex         | HMAC session signing          |

**Usage:**
```bash
./scripts/generate_keys.sh          # Auto-detect: generate if missing
./scripts/generate_keys.sh --init   # First-time generation only
./scripts/generate_keys.sh --rotate # Force-rotate all keys (backs up old .env)
```

- `.env` file permissions are set to `600` (owner read/write only).
- Old keys are backed up to `.env.backup.<timestamp>` before rotation.
- Backup files are also set to mode `600`.

---

## Authentication & Authorisation

**File:** `src/middleware/auth.py`

Admin endpoints (ingestion, verification trigger) are protected by API key
authentication using the `X-Admin-API-Key` header:

- **Constant-time comparison** via `hmac.compare_digest` prevents timing attacks.
- In **development** without a configured key: logs a warning but allows access.
- In **production** without a configured key: returns `503` (service unavailable).
- Invalid or missing key returns `401`/`403` with no information leakage.

Protected endpoints:
- `POST /api/v1/ingestion/*` (all ingestion routes)
- `POST /api/v1/verification/trigger`

---

## CORS Policy

**File:** `src/main.py`

| Environment   | Origins                                               | Credentials | Methods             | Headers                                    |
| ------------- | ----------------------------------------------------- | ----------- | ------------------- | ------------------------------------------ |
| Production    | `HAQSETU_CORS_ORIGINS` (configurable, comma-separated)| Yes         | GET, POST           | Content-Type, Authorization, X-DPDPA-Consent, X-Admin-API-Key |
| Development   | localhost:3000, localhost:8000, 127.0.0.1:8000         | No          | GET, POST, OPTIONS, HEAD | Content-Type, Accept, Authorization, X-DPDPA-Consent, X-Admin-API-Key |

- `allow_credentials=True` is **never** combined with `allow_origins=["*"]`
  (browsers reject this per the CORS specification).
- Production origins default to `https://haqsetu.in,https://www.haqsetu.in`.

---

## Content Security Policy (CSP)

**File:** `src/middleware/privacy.py`

Applied to every response:

```
default-src 'self';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
img-src 'self' data:;
font-src 'self' https://fonts.gstatic.com;
connect-src 'self';
frame-ancestors 'none';
base-uri 'self';
form-action 'self'
```

The following CDN domains are whitelisted because they are used by
`templates/index.html` for Google Fonts:
- `https://fonts.googleapis.com` (stylesheets)
- `https://fonts.gstatic.com` (font files)

---

## Security Headers

**File:** `src/middleware/privacy.py`

Every response includes:

| Header                             | Value                                            |
| ---------------------------------- | ------------------------------------------------ |
| `Strict-Transport-Security`        | `max-age=63072000; includeSubDomains; preload`   |
| `Content-Security-Policy`          | See [CSP section](#content-security-policy-csp)  |
| `X-Content-Type-Options`           | `nosniff`                                        |
| `X-Frame-Options`                  | `DENY`                                           |
| `Referrer-Policy`                  | `strict-origin-when-cross-origin`                |
| `Permissions-Policy`               | camera=(), microphone=(self), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=() |
| `X-Permitted-Cross-Domain-Policies`| `none`                                           |
| `Cache-Control`                    | `no-store, no-cache, must-revalidate, private`   |
| `Pragma`                           | `no-cache`                                       |

---

## Rate Limiting & IP Spoofing Protection

**File:** `src/middleware/rate_limit.py`

- Configurable per-minute rate limit (`RATE_LIMIT_PER_MINUTE`, default: 60).
- Uses **rightmost-minus-N** strategy for `X-Forwarded-For` parsing to prevent
  IP spoofing. The `trusted_proxy_count` setting (default: 1) determines how
  many rightmost IPs are trusted proxies. The client IP is the first untrusted
  IP from the right.

---

## XSS Prevention

**File:** `static/js/app.js`

All dynamic content rendered via `innerHTML` is sanitised through the `esc()`
function, which escapes HTML special characters (`&`, `<`, `>`, `"`, `'`).

Additionally:
- URL attributes (`href`) are validated to ensure they start with `https://` or
  `http://`, blocking `javascript:` protocol injection.
- The `scheme.website` field is only rendered as a clickable link if it passes
  the protocol check.
- Evidence `source_url` values are validated the same way.

---

## SSRF Protection

**File:** `src/services/verification/sansad_client.py`

The `fetch_bill_detail` method accepts external URLs for fetching parliamentary
bill details. To prevent Server-Side Request Forgery:

- **Domain allowlist**: Only the following domains are permitted for external
  fetches:
  - `sansad.in`
  - `www.sansad.in`
  - `rajyasabha.nic.in`
  - `loksabha.nic.in`
- **HTTPS enforcement**: HTTP URLs are rejected.
- **Path traversal check**: URLs containing `..` are rejected.
- **Redirect following disabled**: `follow_redirects=False` on external fetches
  to prevent redirect-based SSRF.

---

## Upload & DoS Protection

**File:** `src/api/v1/query.py`

Audio file uploads for the voice query endpoint are protected against
memory-exhaustion DoS:

- **Streaming size enforcement**: Audio is read in 64 KB chunks. If the
  cumulative size exceeds 10 MB, the request is rejected with `413` **during
  the read**, not after loading the entire file into memory.
- The endpoint also validates that the uploaded file is non-empty.

---

## Container Hardening

### Dockerfile

**File:** `Dockerfile`

- **Multi-stage build**: Builder stage installs dependencies; runtime stage
  contains only what's needed.
- **Non-root user**: Runs as `appuser` (UID 10001), not root.
- **Security updates**: `apt-get upgrade -y` in the runtime stage.
- **Minimal attack surface**: Tests, infrastructure, scripts, Makefile, and
  dotfiles are removed from the final image.
- **Hash collision DoS protection**: `PYTHONHASHSEED=random`.
- **No pip cache**: `PIP_NO_CACHE_DIR=1`.
- **Health check**: Configured at the container level against `/api/v1/health`.
- **Server header**: Overridden to `HaqSetu` (hides uvicorn version).

### Docker Compose (Production)

**File:** `docker-compose.prod.yml`

| Hardening                   | App container | Redis container |
| --------------------------- | :-----------: | :-------------: |
| Read-only root filesystem   | Yes           | Yes             |
| tmpfs for /tmp              | Yes (100 MB)  | Yes (50 MB)     |
| `no-new-privileges`         | Yes           | Yes             |
| Drop ALL capabilities       | Yes           | Yes             |
| Memory limits               | 2 GB          | 1 GB            |
| CPU limits                  | 2 cores       | 1 core          |
| Log rotation                | 50 MB x 5     | 20 MB x 3       |
| Request limit (uvicorn)     | 10,000/worker | --              |

---

## Redis Hardening

**File:** `docker-compose.prod.yml`

- **Authentication required**: `requirepass` set from `${REDIS_PASSWORD}`
  (generated by `scripts/generate_keys.sh`, never a default value).
- **No port exposure**: Redis ports are not published in production.
- **Dangerous commands disabled**: `FLUSHDB`, `FLUSHALL`, `DEBUG`, and `CONFIG`
  are renamed to empty strings.
- **Memory policy**: `allkeys-lru` with 512 MB cap.
- **Persistence**: AOF with `appendfsync everysec`.

---

## Infrastructure (Terraform)

**File:** `infrastructure/terraform/main.tf`

- **GCS bucket CORS**: Uses a configurable `cors_allowed_origins` variable
  (defaults to `https://haqsetu.in`, `https://www.haqsetu.in`). Previously
  was `["*"]`.
- **Health probes**: Startup and liveness probes point to `/api/v1/health`
  (the actual health endpoint).
- **Cloud Run IAM**: `allUsers` invoker is intentional for this public-facing
  civic assistant. Access control is enforced at the application layer.

---

## Deployment Safety

**File:** `scripts/deploy.sh`

- **Production terraform requires interactive approval**: In production,
  `terraform plan` runs first, then the operator must type `yes` to proceed.
  Development environments still use `-auto-approve` for convenience.
- **`--allow-unauthenticated` is documented**: The Cloud Run deployment
  intentionally allows unauthenticated access because HaqSetu is a public
  civic tool. The comment in the script explains this design decision.

---

## Error Handling

**Files:** `src/api/v1/ingestion.py`, `src/api/v1/query.py`, `src/api/v1/verification.py`

- Error messages returned to clients are **generic** (e.g., "Ingestion failed.
  Check server logs for details."). Stack traces and exception details are
  logged server-side only.
- `from None` is used on re-raised `HTTPException`s to suppress the original
  traceback in error responses.

---

## DPDPA Compliance & Privacy

**File:** `src/middleware/privacy.py`

The Digital Personal Data Protection Act (DPDPA) middleware:

- **PII sanitisation**: Aadhaar numbers, phone numbers, and email addresses are
  masked in structured logs (e.g., `1234 5678 9012` becomes `XXXX-XXXX-9012`).
- **Consent tracking**: The `X-DPDPA-Consent` header is logged per request.
- **Privacy headers**: `X-DPDPA-Compliant`, `X-Data-Processing-Purpose`,
  `X-Data-Retention-Policy` are set on every response.
- **No caching of PII**: `Cache-Control: no-store, no-cache, must-revalidate, private`.

---

## CDN & External Domain Whitelist

### CDN Domains (CSP-whitelisted)

| Domain                       | Purpose                    | CSP Directive |
| ---------------------------- | -------------------------- | ------------- |
| `https://fonts.googleapis.com` | Google Fonts stylesheets | `style-src`   |
| `https://fonts.gstatic.com`   | Google Fonts files        | `font-src`    |

### Government API Domains (backend HTTP clients)

| Domain                        | Client                    | Purpose                          |
| ----------------------------- | ------------------------- | -------------------------------- |
| `https://www.myscheme.gov.in` | `MySchemeClient`          | Government scheme data ingestion |
| `https://api.data.gov.in`     | `DataGovClient`           | Open government data API         |
| `https://egazette.gov.in`     | `GazetteClient`           | Gazette of India notifications   |
| `https://www.indiacode.nic.in`| `IndiaCodeClient`         | Indian legal code lookup         |
| `https://sansad.in`           | `SansadClient`            | Parliament records and bills     |

### SSRF-Allowed External Domains (sansad_client.py)

| Domain                  | Purpose                            |
| ----------------------- | ---------------------------------- |
| `sansad.in`             | Main Sansad portal                 |
| `www.sansad.in`         | WWW variant                        |
| `rajyasabha.nic.in`     | Rajya Sabha (Upper House) records  |
| `loksabha.nic.in`       | Lok Sabha (Lower House) records    |

---

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly by
emailing **support@haqsetu.in** with details. Do not open a public issue for
security vulnerabilities.
