// GitHub Release 资产镜像：`/releases/download/v{version}/{filename}`
//
// 用途：GitHub Releases 在中国大陆不可直连，这里用 R2 桶镜像发布资产，
// URL 形态与 GitHub 完全同形、只换 base：
//   https://revpi.dev/releases/download/v0.1.0/rpi-0.1.0-x86_64-unknown-linux-gnu.tar.gz
//
// R2 契约：桶名 `rpi-releases`（wrangler.toml 的 `RELEASES` binding），
// 对象 key = `v{version}/{filename}`；资产命名与 rpi 仓库
// .github/workflows/build.yml 一致（6 目标 × 资产 + .sha256 sidecar），
// 由 scripts/mirror-release.sh 在发版时从 GitHub Release 逐个上传。
// 单资产约 30MB，超 Pages 25MiB 静态资产上限，因此必须走 R2 而非静态文件。
//
// 严格校验 key 形状，不匹配一律 404 —— 防止借该路由读取桶内任意对象。
const KEY_PATTERN =
  /^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?\/rpi-[0-9A-Za-z.+-]+-(x86_64-pc-windows-msvc|aarch64-apple-darwin|x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl|aarch64-unknown-linux-musl|aarch64-unknown-linux-gnu)\.(tar\.gz|zip)(\.sha256)?$/;

function notFound() {
  return new Response(null, { status: 404 });
}

function headersFor(key, object) {
  const headers = new Headers();
  headers.set(
    "Content-Type",
    key.endsWith(".sha256")
      ? "text/plain; charset=utf-8"
      : "application/octet-stream",
  );
  headers.set("Content-Length", String(object.size));
  headers.set("ETag", object.httpEtag);
  // 版本化资产内容不可变，允许长期缓存
  headers.set("Cache-Control", "public, max-age=31536000, immutable");
  return headers;
}

function keyFrom(params) {
  const key = (params.path ?? []).join("/");
  return KEY_PATTERN.test(key) ? key : null;
}

export async function onRequestGet({ env, params }) {
  const key = keyFrom(params);
  if (!key || !env.RELEASES) {
    return notFound();
  }
  const object = await env.RELEASES.get(key);
  if (!object) {
    return notFound();
  }
  return new Response(object.body, { headers: headersFor(key, object) });
}

export async function onRequestHead({ env, params }) {
  const key = keyFrom(params);
  if (!key || !env.RELEASES) {
    return notFound();
  }
  const object = await env.RELEASES.head(key);
  if (!object) {
    return notFound();
  }
  return new Response(null, { headers: headersFor(key, object) });
}
