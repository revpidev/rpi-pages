// Official-site proxy for GitHub Release assets: `/releases/download/v{version}/{filename}`
//
// Purpose: GitHub Releases are unreachable from some networks (mainland China);
// this Pages Function pulls from GitHub at the Cloudflare edge and streams the
// bytes through, so users only need access to revpi.dev. The URL shape matches
// GitHub exactly with only the base swapped:
//   https://revpi.dev/releases/download/v0.1.0/rpi-0.1.0-x86_64-unknown-linux-gnu.tar.gz
//
// Why not R2/static files: a single asset is ~30MB (~15MB compressed) — under
// the Pages 25MiB static limit, but committing them would grow the repository
// by ~180MB per release; R2 requires a payment method on the account. The
// proxy keeps zero storage, zero cost, and zero extra release steps (the
// mirror is live as soon as the GitHub Release assets exist). Asset naming
// matches the rpi repository's .github/workflows/build.yml: six targets x
// (mini archive, standard/full bundles, musl `-static` fallback) + `.sha256`
// sidecars (V18-13).
//
// Resumable downloads (V18-12/V18-13): the `Range` and `If-Range` headers are
// forwarded upstream so the client's `curl -C -` continues across mirrors, and
// the upstream status passes through (200 full body, 206 partial, 416 range
// not satisfiable — the client re-verifies the sha256 before re-downloading).
//
// Strict key-shape validation; anything else is a 404 — this route must never
// become an open proxy for arbitrary upstream paths.
const TARGETS =
  "(x86_64-pc-windows-msvc|aarch64-apple-darwin|x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl|aarch64-unknown-linux-musl|aarch64-unknown-linux-gnu)";
const KEY_PATTERN = new RegExp(
  `^v\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.]+)?/rpi-\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.]+)?-${TARGETS}(-standard|-full|-static)?\\.(tar\\.gz|zip)(\\.sha256)?$`,
);

const UPSTREAM_BASE =
  "https://github.com/revpidev/rpi/releases/download";

// Statuses whose semantics the client depends on; everything else is a proxy
// error. 416 must not be normalized: it tells the resumable downloader that
// the cached partial is at/after EOF.
const PASS_THROUGH_STATUSES = new Set([200, 206, 416]);

// Error responses must be no-store: if an edge cached a transient upstream
// miss (assets upload one by one during a release), the default max-age would
// keep serving 404s long after the assets became available.
function errorResponse(status) {
  const headers = new Headers();
  headers.set("Cache-Control", "no-store");
  return new Response(null, { status, headers });
}

function notFound() {
  return errorResponse(404);
}

function keyFrom(params) {
  const key = (params.path ?? []).join("/");
  return KEY_PATTERN.test(key) ? key : null;
}

// Upstream request headers: the proxy's own User-Agent plus the two headers a
// resumable download needs. No other client header is forwarded, so this route
// cannot be used to inject request state into GitHub.
function upstreamHeaders(request) {
  const headers = { "User-Agent": "revpi.dev release proxy" };
  if (!request) {
    return headers;
  }
  for (const name of ["Range", "If-Range"]) {
    const value = request.headers.get(name);
    if (value) {
      headers[name] = value;
    }
  }
  return headers;
}

function headersFor(key, upstream) {
  const headers = new Headers();
  headers.set(
    "Content-Type",
    key.endsWith(".sha256")
      ? "text/plain; charset=utf-8"
      : "application/octet-stream",
  );
  // `Content-Range`/`Accept-Ranges`/`Content-Length` belong to the Range
  // protocol: the client sizes its progress bar from them and decides from
  // `Accept-Ranges` whether a partial resume is even possible.
  for (const name of [
    "content-length",
    "etag",
    "last-modified",
    "content-range",
    "accept-ranges",
  ]) {
    const value = upstream.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }
  // Versioned asset URLs are *mostly* immutable, but tag re-pushes are a
  // sanctioned operation in this project (RELEASING.md: a re-pushed tag
  // refreshes both assets and notes) and replace asset bytes under the same
  // URL. A year-long immutable edge cache would then keep serving stale
  // payloads whose sha256 no longer matches the refreshed sidecars (observed
  // once during the v0.1.4 release re-push). Cap the edge TTL at one hour so
  // a re-push self-heals quickly; the extra origin fetches are negligible.
  // A 206 (or a 416) is a per-request answer keyed by the client's Range and
  // validator, so it must never be stored at the edge.
  headers.set(
    "Cache-Control",
    upstream.status === 200 ? "public, max-age=3600" : "no-store",
  );
  return headers;
}

async function proxy(key, method, request) {
  // GitHub answers release assets with a 302 to its CDN; fetch follows by
  // default. The body is streamed through, never buffered in the Worker.
  const upstream = await fetch(`${UPSTREAM_BASE}/${key}`, {
    method,
    redirect: "follow",
    headers: upstreamHeaders(request),
  });
  if (!PASS_THROUGH_STATUSES.has(upstream.status)) {
    // Upstream 404 (version/asset doesn't exist) passes the semantics through
    // verbatim; every other upstream error normalizes to 502.
    return upstream.status === 404 ? notFound() : errorResponse(502);
  }
  return new Response(method === "HEAD" ? null : upstream.body, {
    status: upstream.status,
    headers: headersFor(key, upstream),
  });
}

export async function onRequestGet({ request, params }) {
  const key = keyFrom(params);
  if (!key) {
    return notFound();
  }
  return proxy(key, "GET", request);
}

export async function onRequestHead({ request, params }) {
  const key = keyFrom(params);
  if (!key) {
    return notFound();
  }
  return proxy(key, "HEAD", request);
}
