# revpi.dev — rpi product endpoints (Cloudflare Pages)

Since ADR-0009, the default values of rpi's five product endpoints point at `revpi.dev`. This repository is the deployment repository for the rpi website / product endpoints (Cloudflare Pages project `revpi`): static directory content plus Pages Functions, with no backend.

> Repository split (the rpi source repository and this deployment repository are independent; requirements / design / ADR documents live in a private documentation repository and are not published): this site is **rpi-pages**; the rpi source lives in the **rpi** repository. The endpoint contract and deployment notes are in this README. `index.html` is the site home page (open-source project page with features / quick start / endpoint notes); `/` requests hit it directly.

## Endpoint → file mapping

| Endpoint | Implementation | Notes |
|---|---|---|
| `GET /api/models/providers/{id}`, `GET /api/models/providers/{id}.json` | `api/models/providers/*.json` (generated) + `functions/api/models/providers/[id].js` (extensionless, the client's request shape) + `functions/api/models/providers/[id].json.js` (documented shape) | Model-catalog overlay, `{"models":[...]}`; static assets carry ETag/Last-Modified automatically, and client `If-None-Match` revalidation in the 4h window returns 304; an unknown provider returns 404 = "overlay unavailable" (proxied by `functions/api/models/providers/[id].js` / `[id].json.js`; the SPA fallback would otherwise serve index.html, so the function distinguishes by content-type) |
| `GET /api/latest-version` | `api/latest-version.json` (generated) | Version check `{"version","packageName","note"}`; updated on release; the extensionless path is rewritten by `_redirects` |
| `GET /api/report-install` | `functions/api/report-install.js` | Telemetry sink, 204; binding KV `ANALYTICS` records install statistics |
| `GET /install.sh`, `GET /install.ps1` | `install.sh`, `install.ps1` (static files at the site root) | Install scripts; the single source of truth is the rpi repository, copied by `scripts/generate-site.py` |
| `GET /releases/download/v{ver}/{file}` | `functions/releases/download/[[path]].js` | GitHub Release asset proxy on the official site (same URL shape as GitHub, only the base changes; edge nodes fetch from GitHub when it is unreachable); the key shape is validated strictly, and both misses and upstream 404s return 404; zero storage, zero extra release steps |
| `GET /api/extensions/index.json` | `api/extensions/index.json` (generated) | Extension index (used by the directory page and `rpi list`); schema in extension-distribution §5.2; generated from `registry/*.json` entries plus each repository's GitHub Releases |
| `GET /api/extensions/{name}.json` | `api/extensions/<name>.json` (generated) + `functions/api/extensions/[name].json.js` | Per-extension version matrix (used by `rpi install/info` for version resolution); an unknown name returns a bare 404 (the function prevents the SPA fallback, same as providers) |
| `GET /api/extensions/allowlist.json` | `api/extensions/allowlist.json` (generated) | Repository allowlist for the download proxy (the full repository set of the index), validated by `functions/extensions/download` |
| `GET /oauth/client.json`, `GET /oauth/{callbackId}/client.json` | `functions/oauth/[[path]].js` | rpi's Client ID Metadata Documents (the MCP OAuth `clientRegistration: "cimd"` surface): shared document + per-server documents, shaped like the upstream pi.dev service (`callbackId` = the first 9 bytes of the MCP server URL SHA-256, base64url, 12 characters); malformed ids/paths return a bare 404 |
| `GET /extensions/download/{owner}/{repo}/{tag}/{file}` | `functions/extensions/download/[[path]].js` | `.rpix` extension asset proxy; owner/repo must be in the allowlist (otherwise 404, preventing an open relay); tag is strictly `v<semver>`, file is strictly `*.rpix[.sha256]`; streams from GitHub, upstream 404→404, immutable caching |
| `/extensions` | `extensions.html` | Extension directory: the client reads index.json and renders cards (official badge, capabilities, L0 trust notice, install-command copy) |
| `/session/#{gistId}` | `session/index.html` | Share viewer; the gist id is in the URL fragment (`get_share_viewer_url` joins `#`), and the page JS reads and renders the raw gist |
| `/changelog` | `changelog.html` | Changelog link in the update banner |

## Generation + deployment

### Deployment: Cloudflare Pages Git integration (push to deploy)

The repository is connected to the Pages project `revpi`: pushing to `main` deploys production, and PRs create preview environments. Build configuration (entered in the console when connecting; see the steps below):

| Setting | Value | Notes |
|---|---|---|
| Production branch | `main` | Production branch |
| Build command | empty | Generated `api/*.json` artifacts are committed; the build environment has no `../rpi` source, so running the generator would fail |
| Output directory | `.` | The repository root is the site root |

> **Note**: do not run `npx wrangler pages deploy` manually after connecting — manual deploys and the Git integration overwrite each other (the last deploy wins), leaving production and the repository inconsistent. Use `npx wrangler pages dev .` for local preview.

Console connection steps (one-time):

1. dash.cloudflare.com → Workers & Pages → select the `revpi` project
2. Settings → Builds & deployments → **Connect to Git** → authorize GitHub
3. Select `revpidev/rpi-pages`, fill in the build configuration above → Save and Deploy
4. Wait for the first build and verify https://revpi.pages.dev and https://revpi.dev

### Data updates (release flow)

The catalog and latest-version are generated by the rpi source repository. To update the data:

```bash
# 1. Generate the catalog and latest-version, and sync install.sh/install.ps1
#    to the site root (the rpi source repository defaults to a sibling
#    `../rpi`; override with --rpi-repo; the version defaults to the rpi
#    workspace Cargo.toml)
#    WARNING: do not run bare during an RC window: the workspace version is a
#    prerelease (e.g. 0.1.4-rc.4), and the stable endpoint's prerelease shape
#    gate fails the run — pass `--version <stable>` explicitly in that case
#    (RC releases use --rc-version and never touch the stable endpoint);
#    writing a prerelease to the stable endpoint requires an explicit
#    --allow-prerelease (do not use it).
#    During an RC window refresh only the RC endpoint:
#    `--rc-version <stable>-rc.N` (the stable endpoint is skipped
#    automatically; no extra flag needed).
python3 scripts/generate-site.py
#    On release: python3 scripts/generate-site.py --version 0.2.0 --note "..."

# 2. Commit and push — the Git integration deploys automatically
cd /home/leven/develop/ai/revpi/rpi-pages
git add api/ install.sh install.ps1
git commit -m "chore(api): sync catalog / latest-version"
git push
```

### Extension registry (`registry/`)

`registry/<name>.json` is the **source data** for the extension index (one entry per extension: `name/repository/description/author/license`; first-party entries add `"official": true`; optional `"yankedVersions": [...]` overrides yanks, and `"lockstepHost": true` means the extension is released in lockstep with the host — generation backfills each version's `minHostVersion` to that version itself; an explicit `minHostVersion` in the entry takes precedence; CI injects the same value into the `.rpix` manifest at pack time). **Transitional note**: the design (extension-distribution §5.1) assigns the index to a separate repository, `revpidev/rpi-plugins`, which does not exist yet; for now the index data lives in this repository's `registry/` and will move once that repository exists, with the generator switched to read it.

`generate-site.py` reads `registry/*.json`, calls the GitHub Releases API for each `repository` to enumerate the version matrix (matching assets exactly as `<name>-<version>[-<target>].rpix`, trusting the `<file>.sha256` sidecar in the same Release), and produces `api/extensions/{index,allowlist,<name>}.json` — commit means deploy, on the same pipeline as the catalog. `GITHUB_TOKEN` is optional (anonymous requests are limited to 60/hour); if the API is unreachable, the affected extension degrades to `versions: []` with a warning instead of failing the whole generation. First-party extension description/capabilities/kind come from each crate's `rpi-extension.json` in `../rpi`.

> Release asset mirroring (`/releases/download/*`) is a Function proxy back to GitHub: zero storage, zero extra release steps — assets are usable as soon as the GitHub Release is published.

### First-time project creation (legacy flow, archived)

```bash
npx wrangler pages project create revpi --production-branch main
# Note: wrangler 4.x uses the current working directory's `functions` folder,
# so deploy from the rpi-pages repository root (deploying from a parent
# directory would lose functions)
npx wrangler pages deploy . --project-name=revpi --branch main
# Custom domain (DNS is configured automatically when hosted on Cloudflare):
#   Pages console → Custom domains → add revpi.dev (already done)
```

### (Optional) telemetry statistics

```bash
npx wrangler kv namespace create ANALYTICS   # put the id into wrangler.toml and redeploy
```

## Client side

The default endpoints already point at revpi.dev in code (ADR-0009). Each can still be overridden:

```bash
RPI_MODEL_CATALOG_URL=https://revpi.dev        # or settings.json modelCatalogUrl
RPI_VERSION_CHECK_URL=https://revpi.dev/api/latest-version
RPI_TELEMETRY_URL=https://revpi.dev/api/report-install
RPI_SHARE_VIEWER_URL=https://revpi.dev/session
# The literal value off disables the endpoint; RPI_OFFLINE disables all of them
```

## Compatibility notes

- **Last-Modified must be later than the local builtin `generated_at`**: `remote_models` ignores the overlay when `last_modified <= local_generated_at` (using local data). A static asset's Last-Modified is the deployment time, which naturally satisfies this; redeploying refreshes it.
- **radius is not in the catalog**: radius is a dynamic gateway provider (default `https://radius.pi.dev`, an upstream-hosted service rather than static content); `/api/models/providers/radius` returning 404 is the "overlay unavailable" semantic, and radius users are unaffected.
- **Data source**: the catalog is generated from `crates/rpi-ai/src/providers/data/*.json` in the rpi source repository (`scripts/generate-site.py` reads it, defaulting to the sibling `../rpi`) and is updated with rpi releases.
