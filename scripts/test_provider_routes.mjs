// Regression test for the model-catalog overlay routes:
//   functions/api/models/providers/[id].js       (extensionless — the client)
//   functions/api/models/providers/[id].json.js  (the documented .json form)
//
//     node scripts/test_provider_routes.mjs
//
// The client (remote_catalog_provider.rs) requests the extensionless path,
// so the extensionless route must proxy the generated asset and answer a
// bare 404 for unknown providers (never the SPA fallback).

import assert from "node:assert/strict";
import { onRequestGet as extensionless } from "../functions/api/models/providers/[id].js";
import { onRequestGet as dotJson } from "../functions/api/models/providers/[id].json.js";

let passed = 0;

function assetResponse(body, contentType) {
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": contentType,
      etag: '"abc123"',
      "cache-control": "public, max-age=0, must-revalidate",
    },
  });
}

function envWith({ body, contentType }) {
  return {
    ASSETS: {
      async fetch(url) {
        const path = new URL(url).pathname;
        if (path === "/api/models/providers/anthropic.json") {
          return assetResponse(body, contentType);
        }
        // Cloudflare Pages SPA fallback for any other path.
        return assetResponse("<!doctype html><html></html>", "text/html; charset=utf-8");
      },
    },
  };
}

const catalog = JSON.stringify({ models: [{ id: "claude-haiku-5.5" }] });

for (const [label, handler] of [
  ["extensionless", extensionless],
  ["dot-json", dotJson],
]) {
  // Known provider: the generated JSON is proxied with its headers intact.
  {
    const response = await handler({
      env: envWith({ body: catalog, contentType: "application/json" }),
      params: { id: "anthropic" },
      request: new Request("https://revpi.dev/api/models/providers/anthropic"),
    });
    assert.equal(response.status, 200, label);
    assert.equal(response.headers.get("etag"), '"abc123"', label);
    const body = await response.json();
    assert.equal(body.models[0].id, "claude-haiku-5.5", label);
    passed += 3;
  }
  // Unknown provider: the SPA fallback (HTML) must become a bare 404.
  {
    const response = await handler({
      env: envWith({ body: catalog, contentType: "application/json" }),
      params: { id: "nonexistent-xyz" },
      request: new Request("https://revpi.dev/api/models/providers/nonexistent-xyz"),
    });
    assert.equal(response.status, 404, label);
    passed += 1;
  }
  // Missing id param: 404.
  {
    const response = await handler({
      env: envWith({ body: catalog, contentType: "application/json" }),
      params: {},
      request: new Request("https://revpi.dev/api/models/providers/"),
    });
    assert.equal(response.status, 404, label);
    passed += 1;
  }
}

console.log(`test_provider_routes: ${passed} checks passed`);