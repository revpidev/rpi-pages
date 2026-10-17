// Model-catalog overlay (extensionless): `/api/models/providers/{id}`
//
// The rpi client requests the extensionless path — `catalog_url` in
// crates/rpi/src/core/remote_catalog_provider.rs joins
// `/api/models/providers/{encodeURIComponent(id)}`, exactly like the
// upstream client against pi.dev. Without this route the request falls
// through to the SPA fallback (200 + index.html, as the bare `.json`
// route's comment describes), and the client would silently fall back to
// the built-in catalog on every refresh.
//
// `[id].json.js` keeps serving the `.json` form documented earlier (same
// proxy, same bare-404 contract); both read the same generated asset.
export async function onRequestGet({ env, params, request }) {
  const id = params.id ?? params["id.json"];
  if (!id) {
    return new Response(null, { status: 404 });
  }
  const assetUrl = new URL(`/api/models/providers/${id}.json`, request.url);
  const asset = await env.ASSETS.fetch(assetUrl);
  // ASSETS.fetch 对未命中路径会走 SPA fallback（返回 200 + index.html），
  // 因此以 content-type 区分：不是 JSON 一律视为 catalog 未收录。
  const contentType = asset.headers.get("content-type") || "";
  if (asset.status !== 200 || contentType.includes("text/html")) {
    return new Response(null, { status: 404 });
  }
  return asset;
}