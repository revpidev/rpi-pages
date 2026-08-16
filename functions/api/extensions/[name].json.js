// Extension registry detail: `/api/extensions/{name}.json`
//
// 与 functions/api/models/providers/[id].json.js 同模式：静态回退会把
// 未收录 name SPA-fallback 成 index.html（200 + HTML），破坏
// extension-distribution.md §5.2 的「未知 name 返回裸 404」契约。这里
// 代理存在的静态资产（保留其 ETag / Last-Modified / cache-control），
// 未收录一律裸 404。index.json / allowlist.json 也经此函数透传（静态
// 资产存在，正常返回）。
export async function onRequestGet({ env, params, request }) {
  const name = params.name ?? params["name.json"];
  if (!name) {
    return new Response(null, { status: 404 });
  }
  const assetUrl = new URL(`/api/extensions/${name}.json`, request.url);
  const asset = await env.ASSETS.fetch(assetUrl);
  // ASSETS.fetch 对未命中路径会走 SPA fallback（返回 200 + index.html），
  // 因此以 content-type 区分：不是 JSON 一律视为 registry 未收录。
  const contentType = asset.headers.get("content-type") || "";
  if (asset.status !== 200 || contentType.includes("text/html")) {
    return new Response(null, { status: 404 });
  }
  return asset;
}
