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
// matches the rpi repository's .github/workflows/build.yml (6 targets ×
// asset + .sha256 sidecar).
//
// Strict key-shape validation; anything else is a 404 — this route must never
// become an open proxy for arbitrary upstream paths.
const KEY_PATTERN =
  /^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?\/rpi-[0-9A-Za-z.+-]+-(x86_64-pc-windows-msvc|aarch64-apple-darwin|x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl|aarch64-unknown-linux-musl|aarch64-unknown-linux-gnu)\.(tar\.gz|zip)(\.sha256)?$/;

const UPSTREAM_BASE =
  "https://github.com/revpidev/rpi/releases/download";

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

function headersFor(key, upstream) {
  const headers = new Headers();
  headers.set(
    "Content-Type",
    key.endsWith(".sha256")
      ? "text/plain; charset=utf-8"
      : "application/octet-stream",
  );
  for (const name of ["content-length", "etag"]) {
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
  headers.set("Cache-Control", "public, max-age=3600");
  return headers;
}

async function proxy(key, method) {
  // GitHub answers release assets with a 302 to its CDN; fetch follows by
  // default. The body is streamed through, never buffered in the Worker.
  const upstream = await fetch(`${UPSTREAM_BASE}/${key}`, {
    method,
    redirect: "follow",
    headers: { "User-Agent": "revpi.dev release proxy" },
  });
  if (!upstream.ok) {
    // Upstream 404 (version/asset doesn't exist) passes the semantics through
    // verbatim; every other upstream error normalizes to 502.
    return upstream.status === 404 ? notFound() : errorResponse(502);
  }
  return new Response(upstream.body, {
    headers: headersFor(key, upstream),
  });
}

export async function onRequestGet({ params }) {
  const key = keyFrom(params);
  if (!key) {
    return notFound();
  }
  return proxy(key, "GET");
}

export async function onRequestHead({ params }) {
  const key = keyFrom(params);
  if (!key) {
    return notFound();
  }
  const upstream = await proxy(key, "HEAD");
  return new Response(null, upstream);
}
