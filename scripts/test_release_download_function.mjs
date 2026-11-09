// Regression test for the official-site Release asset proxy (V18-13):
//
//     node --test scripts/test_release_download_function.mjs
//
// The proxy (functions/releases/download/[[path]].js) is the only place where
// the site's download mirror touches the Range protocol, so it is exercised
// directly instead of through the site generator's tests: strict key shapes
// (including the V18-13 `-standard` / `-full` / `-static` suffixes),
// `Range`/`If-Range` forwarding, upstream status pass-through (200/206/416)
// and the response headers the resumable installers depend on. No network:
// `fetch` is stubbed and restored around every case.

import assert from "node:assert/strict";
import test from "node:test";

import {
  onRequestGet,
  onRequestHead,
} from "../functions/releases/download/[[path]].js";

const UPSTREAM_BASE = "https://github.com/revpidev/rpi/releases/download";
const realFetch = globalThis.fetch;

function stubFetch(t, handler) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const call = { url, init };
    calls.push(call);
    return handler(call);
  };
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  return calls;
}

function upstreamResponse({ status = 200, headers = {}, body = null } = {}) {
  return new Response(body, { status, headers });
}

function siteRequest(key, headers = {}) {
  return new Request(`https://revpi.dev/releases/download/${key}`, { headers });
}

function pathParams(key) {
  return { path: key.split("/") };
}

// --- key shapes ---------------------------------------------------------

const ACCEPTED_KEYS = [
  // mini base archives (all six targets, both archive types)
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  "v0.1.8/rpi-0.1.8-x86_64-pc-windows-msvc.zip",
  "v0.1.8/rpi-0.1.8-aarch64-apple-darwin.tar.gz",
  // profile bundles
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-standard.tar.gz",
  "v0.1.8/rpi-0.1.8-aarch64-apple-darwin-full.tar.gz",
  "v0.1.8/rpi-0.1.8-aarch64-unknown-linux-musl-standard.tar.gz",
  "v0.1.8/rpi-0.1.8-x86_64-pc-windows-msvc-full.zip",
  // musl static fallback
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-musl-static.tar.gz",
  "v0.1.8/rpi-0.1.8-aarch64-unknown-linux-musl-static.tar.gz",
  // sidecars
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz.sha256",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-standard.tar.gz.sha256",
  "v0.1.8/rpi-0.1.8-aarch64-apple-darwin-full.tar.gz.sha256",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-musl-static.tar.gz.sha256",
  "v0.1.8/rpi-0.1.8-x86_64-pc-windows-msvc.zip.sha256",
  // prerelease (RC) tag
  "v0.1.8-rc.1/rpi-0.1.8-rc.1-aarch64-unknown-linux-gnu-full.tar.gz",
];

const REJECTED_KEYS = [
  // arbitrary upstream repository / extra path segments
  "other/repo/v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  "v0.1.8/revpidev/rpi/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  // bad version tags
  "0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  "v0.1/rpi-0.1-x86_64-unknown-linux-gnu.tar.gz",
  "v0.1.8.1/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  "latest/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  "v0.1.8-/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  // bad targets
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux.tar.gz",
  "v0.1.8/rpi-0.1.8-i686-unknown-linux-gnu.tar.gz",
  "v0.1.8/rpi-0.1.8-x86_64-apple-darwin-standard.zip",
  // unknown suffixes
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-minimal.tar.gz",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-debug.sha256",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.xz",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.dmg",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-full.tar.gz.asc",
  // path traversal and unrelated files
  "v0.1.8/../../etc/passwd",
  "..%2Fv0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz",
  "v0.1.8/..",
  "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz/extra",
  "",
];

test("accepted key shapes proxy the exact upstream URL", async (t) => {
  const calls = stubFetch(t, () => upstreamResponse({ body: "payload" }));
  for (const key of ACCEPTED_KEYS) {
    const response = await onRequestGet({ params: pathParams(key) });
    assert.equal(response.status, 200, key);
    assert.equal(calls.at(-1).url, `${UPSTREAM_BASE}/${key}`, key);
    await response.arrayBuffer(); // drain the streamed body
  }
  assert.equal(calls.length, ACCEPTED_KEYS.length);
});

test("rejected key shapes are bare 404s and never reach upstream", async (t) => {
  const calls = stubFetch(t, () => upstreamResponse({ body: "payload" }));
  for (const key of REJECTED_KEYS) {
    const response = await onRequestGet({ params: pathParams(key) });
    assert.equal(response.status, 404, JSON.stringify(key));
    assert.equal(response.body, null, JSON.stringify(key));
    const headResponse = await onRequestHead({ params: pathParams(key) });
    assert.equal(headResponse.status, 404, JSON.stringify(key));
  }
  assert.equal(calls.length, 0);
});

test("a missing params.path is a bare 404", async (t) => {
  const calls = stubFetch(t, () => upstreamResponse({ body: "payload" }));
  assert.equal((await onRequestGet({ params: {} })).status, 404);
  assert.equal((await onRequestHead({ params: {} })).status, 404);
  assert.equal(calls.length, 0);
});

// --- Range / If-Range forwarding ----------------------------------------

test("Range and If-Range are forwarded, other client headers are not", async (t) => {
  const calls = stubFetch(t, () =>
    upstreamResponse({
      status: 206,
      headers: { "content-range": "bytes 0-1/9" },
      body: "xy",
    }),
  );
  const request = siteRequest(
    "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-standard.tar.gz",
    {
      Range: "bytes=0-1",
      "If-Range": '"etag-123"',
      Authorization: "Bearer secret",
      "X-Client-Secret": "leak",
      "Accept-Encoding": "br",
    },
  );
  const response = await onRequestGet({
    request,
    params: pathParams("v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-standard.tar.gz"),
  });
  assert.equal(response.status, 206);

  const forwarded = calls[0].init.headers;
  assert.equal(forwarded.Range, "bytes=0-1");
  assert.equal(forwarded["If-Range"], '"etag-123"');
  assert.equal(forwarded["User-Agent"], "revpi.dev release proxy");
  for (const name of ["Authorization", "X-Client-Secret", "Accept-Encoding"]) {
    assert.equal(forwarded[name], undefined, `${name} must not be forwarded`);
  }
});

test("absent Range/If-Range headers are not invented", async (t) => {
  const calls = stubFetch(t, () => upstreamResponse({ body: "payload" }));
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz";
  await onRequestGet({ request: siteRequest(key), params: pathParams(key) });
  const forwarded = calls[0].init.headers;
  assert.deepEqual(Object.keys(forwarded), ["User-Agent"]);
});

test("a params-only call still forwards a User-Agent upstream", async (t) => {
  const calls = stubFetch(t, () => upstreamResponse({ body: "payload" }));
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu.tar.gz";
  assert.equal((await onRequestGet({ params: pathParams(key) })).status, 200);
  assert.deepEqual(calls[0].init.headers, {
    "User-Agent": "revpi.dev release proxy",
  });
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.redirect, "follow");
});

// --- upstream status pass-through ---------------------------------------

test("200, 206 and 416 pass through; 404 stays 404; other errors are 502", async (t) => {
  const cases = [
    { upstream: 200, expected: 200 },
    { upstream: 206, expected: 206 },
    { upstream: 416, expected: 416 },
    { upstream: 404, expected: 404 },
    { upstream: 500, expected: 502 },
    { upstream: 503, expected: 502 },
  ];
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-full.tar.gz";
  let upstreamStatus = 200;
  stubFetch(t, () =>
    upstreamResponse({
      status: upstreamStatus,
      headers: { "content-range": "bytes 0-1/9" },
      body: upstreamStatus === 416 || upstreamStatus === 404 ? null : "body",
    }),
  );
  for (const { upstream, expected } of cases) {
    upstreamStatus = upstream;
    const response = await onRequestGet({ params: pathParams(key) });
    assert.equal(response.status, expected, `upstream ${upstream}`);
    if (expected === 502 || expected === 404) {
      // Synthesized errors never reuse the upstream body.
      assert.equal(await response.text(), "", `upstream ${upstream}`);
      assert.equal(response.headers.get("cache-control"), "no-store");
    }
  }
});

test("a 416 answer is not routed into the error path", async (t) => {
  const calls = stubFetch(t, () =>
    upstreamResponse({
      status: 416,
      headers: { "content-range": "bytes */1234", "accept-ranges": "bytes" },
    }),
  );
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-musl-static.tar.gz";
  const response = await onRequestGet({
    request: siteRequest(key, { Range: "bytes=9999-" }),
    params: pathParams(key),
  });
  assert.equal(calls.length, 1);
  assert.equal(response.status, 416);
  assert.equal(response.headers.get("content-range"), "bytes */1234");
});

// --- response headers ----------------------------------------------------

test("range and validator headers are forwarded with 200 caching", async (t) => {
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-standard.tar.gz";
  const upstreamHeaders = {
    etag: '"asset-etag"',
    "last-modified": "Fri, 07 Nov 2025 10:00:00 GMT",
    "content-length": "1234",
    "content-range": "bytes 0-1233/1234",
    "accept-ranges": "bytes",
  };
  stubFetch(t, () =>
    upstreamResponse({ status: 200, headers: upstreamHeaders, body: "payload" }),
  );
  const response = await onRequestGet({ params: pathParams(key) });
  for (const [name, value] of Object.entries(upstreamHeaders)) {
    assert.equal(response.headers.get(name), value, name);
  }
  assert.equal(
    response.headers.get("cache-control"),
    "public, max-age=3600",
  );
  // The edge overrides plain Cache-Control for cacheable archive extensions and
  // falls back to its 4h default; the CDN-scoped headers must carry the TTL.
  assert.equal(
    response.headers.get("cdn-cache-control"),
    "public, max-age=3600",
  );
  assert.equal(
    response.headers.get("cloudflare-cdn-cache-control"),
    "public, max-age=3600",
  );
  assert.equal(
    response.headers.get("content-type"),
    "application/octet-stream",
  );
  await response.arrayBuffer();
});

test("206 is not edge-cacheable and keeps the partial-body headers", async (t) => {
  const key = "v0.1.8/rpi-0.1.8-aarch64-apple-darwin-full.tar.gz";
  stubFetch(t, () =>
    upstreamResponse({
      status: 206,
      headers: {
        etag: '"asset-etag"',
        "last-modified": "Fri, 07 Nov 2025 10:00:00 GMT",
        "content-length": "2",
        "content-range": "bytes 0-1/1234",
        "accept-ranges": "bytes",
      },
      body: "xy",
    }),
  );
  const response = await onRequestGet({
    request: siteRequest(key, { Range: "bytes=0-1" }),
    params: pathParams(key),
  });
  assert.equal(response.status, 206);
  assert.equal(response.headers.get("cache-control"), "no-store");
  // A cached partial keyed by one Range must never answer another Range. The
  // edge ignores plain Cache-Control here, so both CDN-scoped headers must say
  // no-store too (a deployed rc.4 partial answered max-age=14400 without this).
  assert.equal(response.headers.get("cdn-cache-control"), "no-store");
  assert.equal(response.headers.get("cloudflare-cdn-cache-control"), "no-store");
  assert.equal(response.headers.get("content-range"), "bytes 0-1/1234");
  assert.equal(response.headers.get("accept-ranges"), "bytes");
  assert.equal(response.headers.get("etag"), '"asset-etag"');
  assert.equal(response.headers.get("last-modified"), "Fri, 07 Nov 2025 10:00:00 GMT");
  assert.equal(response.headers.get("content-length"), "2");
  assert.equal(await response.text(), "xy");
});

test(".sha256 sidecars keep their text content type", async (t) => {
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-musl-standard.tar.gz.sha256";
  const body = `${"a".repeat(64)}  rpi-0.1.8-x86_64-unknown-linux-musl-standard.tar.gz\n`;
  stubFetch(t, () => upstreamResponse({ body }));
  const response = await onRequestGet({ params: pathParams(key) });
  assert.equal(
    response.headers.get("content-type"),
    "text/plain; charset=utf-8",
  );
  assert.equal(await response.text(), body);
});

// --- HEAD ----------------------------------------------------------------

test("HEAD returns no body and preserves status and headers", async (t) => {
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-gnu-full.tar.gz";
  const calls = stubFetch(t, () =>
    upstreamResponse({
      status: 200,
      headers: {
        etag: '"asset-etag"',
        "content-length": "1234",
        "accept-ranges": "bytes",
      },
      body: "payload",
    }),
  );
  const response = await onRequestHead({
    request: siteRequest(key, { "If-Range": '"asset-etag"', Range: "bytes=0-1" }),
    params: pathParams(key),
  });
  assert.equal(calls[0].init.method, "HEAD");
  assert.equal(calls[0].init.headers.Range, "bytes=0-1");
  assert.equal(calls[0].init.headers["If-Range"], '"asset-etag"');
  assert.equal(response.status, 200);
  assert.equal(response.body, null);
  assert.equal(response.headers.get("content-length"), "1234");
  assert.equal(response.headers.get("etag"), '"asset-etag"');
  assert.equal(
    response.headers.get("cache-control"),
    "public, max-age=3600",
  );
});

test("HEAD passes an upstream 206 through without a body", async (t) => {
  const key = "v0.1.8/rpi-0.1.8-x86_64-unknown-linux-musl-static.tar.gz";
  stubFetch(t, () =>
    upstreamResponse({
      status: 206,
      headers: { "content-range": "bytes 0-1/1234" },
    }),
  );
  const response = await onRequestHead({
    request: siteRequest(key, { Range: "bytes=0-1" }),
    params: pathParams(key),
  });
  assert.equal(response.status, 206);
  assert.equal(response.body, null);
  assert.equal(response.headers.get("content-range"), "bytes 0-1/1234");
  assert.equal(response.headers.get("cache-control"), "no-store");
});
