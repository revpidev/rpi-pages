// Regression test for the CIMD documents served by
// functions/oauth/[[path]].js (V17-12; rpi-pages has no JS test runner, this
// is the dependency-free smoke test recorded as release evidence):
//
//     node scripts/test_oauth_function.mjs
//
// It asserts the exact document shape rpi's OAuth flow expects
// (crates/rpi/src/extensions/mcp/oauth.rs) against the upstream pi.dev
// service shape, including the 404 behavior for malformed callback ids.

import assert from "node:assert/strict";
import { onRequestGet } from "../functions/oauth/[[path]].js";

let passed = 0;
function check(name, condition) {
  assert.ok(condition, name);
  passed += 1;
}

async function fetchDoc(segments) {
  return onRequestGet({ params: { path: segments } });
}

// --- shared document: /oauth/client.json --------------------------------
{
  const response = await fetchDoc(["client.json"]);
  check("shared doc status", response.status === 200);
  check(
    "shared doc content type",
    (response.headers.get("content-type") || "").startsWith("application/json"),
  );
  const body = await response.json();
  assert.deepEqual(body, {
    client_id: "https://revpi.dev/oauth/client.json",
    client_name: "rpi",
    client_uri: "https://revpi.dev",
    logo_uri: "https://revpi.dev/favicon.svg",
    application_type: "native",
    redirect_uris: [
      "http://127.0.0.1/callback",
      "http://localhost/callback",
    ],
    grant_types: ["authorization_code", "refresh_token"],
    response_types: ["code"],
    token_endpoint_auth_method: "none",
    token_endpoint_auth_methods_supported: ["none"],
  });
  passed += 1;
}

// --- server-specific document: /oauth/<callbackId>/client.json ----------
{
  // Web Fetch API normalizes the URL, so the id is a plain base64url string.
  const id = "w9gKTtkB7gWy";
  const response = await fetchDoc([id, "client.json"]);
  check("per-server doc status", response.status === 200);
  const body = await response.json();
  check("per-server client_id", body.client_id === `https://revpi.dev/oauth/${id}/client.json`);
  assert.deepEqual(body.redirect_uris, [
    `http://127.0.0.1/callback/${id}`,
    `http://localhost/callback/${id}`,
  ]);
  passed += 1;
  check("per-server application_type", body.application_type === "native");
  check("per-server auth method", body.token_endpoint_auth_method === "none");
}

// --- malformed paths are bare 404s --------------------------------------
const rejects = [
  [], // no path at all
  ["client.json", "extra"], // extra segment after the shared doc
  ["notavalidid", "client.json"], // 11 characters
  ["AAAAAAAAAAAAA", "client.json"], // 13 characters
  ["!!!!!!!!!!!!", "client.json"], // invalid characters
  ["w9gKTtkB7gWy", "client.json", "extra"],
  ["client.json", "client.json"],
  ["w9gKTtkB7gWy", "other.json"],
  ["."],
  ["..", "client.json"],
];
for (const segments of rejects) {
  const response = await fetchDoc(segments);
  check(`404 for ${JSON.stringify(segments)}`, response.status === 404);
}

// --- single-string params.path form (Cloudflare single segment) ---------
{
  const response = await onRequestGet({ params: { path: "client.json" } });
  check("string path form", response.status === 200);
}

console.log(`test_oauth_function: ${passed} checks passed`);