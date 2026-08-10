// GitHub Release 资产官网代理：`/releases/download/v{version}/{filename}`
//
// 用途：GitHub Releases 在部分网络（中国大陆）不可直连，这里由 Cloudflare
// 边缘节点回源 GitHub 并流式转发，用户只需能访问 revpi.dev。URL 形态与
// GitHub 完全同形、只换 base：
//   https://revpi.dev/releases/download/v0.1.0/rpi-0.1.0-x86_64-unknown-linux-gnu.tar.gz
//
// 为什么不是 R2/静态文件：单资产约 30MB（压缩后约 15MB）虽低于 Pages
// 25MiB 静态上限，但入 git 会让仓库每版膨胀约 180MB；R2 需要账号绑定
// 支付方式。代理回源零存储、零费用、发版零额外步骤（GitHub Release
// 发布即镜像可用）。资产命名与 rpi 仓库 .github/workflows/build.yml 一致
// （6 目标 × 资产 + .sha256 sidecar）。
//
// 严格校验 key 形状，不匹配一律 404 —— 防止借该路由代理任意上游路径。
const KEY_PATTERN =
  /^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?\/rpi-[0-9A-Za-z.+-]+-(x86_64-pc-windows-msvc|aarch64-apple-darwin|x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl|aarch64-unknown-linux-musl|aarch64-unknown-linux-gnu)\.(tar\.gz|zip)(\.sha256)?$/;

const UPSTREAM_BASE =
  "https://github.com/revpidev/rpi/releases/download";

function notFound() {
  return new Response(null, { status: 404 });
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
  // 版本化资产内容不可变，允许边缘与客户端长期缓存
  headers.set("Cache-Control", "public, max-age=31536000, immutable");
  return headers;
}

async function proxy(key, method) {
  // GitHub 对 release 资产返回 302 到 CDN，fetch 默认跟随；body 直接透传
  // 流式转发，不在 Worker 内存中缓冲。
  const upstream = await fetch(`${UPSTREAM_BASE}/${key}`, {
    method,
    redirect: "follow",
    headers: { "User-Agent": "revpi.dev release proxy" },
  });
  if (!upstream.ok) {
    // 上游 404（版本/资产不存在）原样透传语义；其余上游错误归一为 502。
    return upstream.status === 404
      ? notFound()
      : new Response(null, { status: 502 });
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
