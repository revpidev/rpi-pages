// 扩展插件 .rpix 资产官网代理：
//   `/extensions/download/<owner>/<repo>/<tag>/<file>`
// 设计见 extension-distribution.md §6。
//
// 与本体代理（functions/releases/download/[[path]].js）同构、同语义：
// GitHub Releases 在部分网络（中国大陆）不可直连，这里由 Cloudflare 边缘
// 节点回源并流式转发，零存储、零费用。不同点在于本代理面向任意插件仓库，
// 因此必须校验 owner/repo 在索引白名单内（生成的
// api/extensions/allowlist.json，索引全量 repo 集合），否则代理会变成
// 任意 GitHub 内容的开放转发器。
//
// 严格校验各段形状，不匹配一律 404 —— 同本体代理的防滥用边界。
const OWNER_REPO_PATTERN = /^[A-Za-z0-9][A-Za-z0-9.-]*$/;
const TAG_PATTERN = /^v\d+\.\d+\.\d+(-[\w.]+)?$/;
const FILE_PATTERN = /^[\w.-]+\.rpix(\.sha256)?$/;

// 错误响应必须 no-store：上游瞬时缺失（如发版资产逐个上传中）若被边缘
// 缓存，默认 max-age=14400 会让镜像在资产就绪后仍返回 404 长达 4 小时。
function errorResponse(status) {
  const headers = new Headers();
  headers.set("Cache-Control", "no-store");
  return new Response(null, { status, headers });
}

function notFound() {
  return errorResponse(404);
}

// 白名单校验：allowlist.json 是生成脚本产出的静态资产，经 env.ASSETS
// 读取（边缘缓存）。读不到/解析失败按不在名单处理——宁可 404 也不放行。
async function isAllowed(env, request, owner, repo) {
  const assetUrl = new URL("/api/extensions/allowlist.json", request.url);
  const asset = await env.ASSETS.fetch(assetUrl);
  if (asset.status !== 200) {
    return false;
  }
  try {
    const allowlist = await asset.json();
    const repositories = allowlist.repositories ?? [];
    return repositories.includes(`${owner}/${repo}`);
  } catch {
    return false;
  }
}

function keyFrom(params) {
  const parts = params.path ?? [];
  if (parts.length !== 4) {
    return null;
  }
  const [owner, repo, tag, file] = parts;
  if (
    !OWNER_REPO_PATTERN.test(owner) ||
    !OWNER_REPO_PATTERN.test(repo) ||
    !TAG_PATTERN.test(tag) ||
    !FILE_PATTERN.test(file)
  ) {
    return null;
  }
  return { owner, repo, tag, file };
}

function headersFor(key, upstream) {
  const headers = new Headers();
  headers.set(
    "Content-Type",
    key.file.endsWith(".sha256")
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
  const upstream = await fetch(
    `https://github.com/${key.owner}/${key.repo}/releases/download/${key.tag}/${key.file}`,
    {
      method,
      redirect: "follow",
      headers: { "User-Agent": "revpi.dev extension proxy" },
    },
  );
  if (!upstream.ok) {
    // 上游 404（tag/资产不存在）原样透传语义；其余上游错误归一为 502。
    return upstream.status === 404 ? notFound() : errorResponse(502);
  }
  return new Response(upstream.body, {
    headers: headersFor(key, upstream),
  });
}

export async function onRequestGet({ env, params, request }) {
  const key = keyFrom(params);
  if (!key || !(await isAllowed(env, request, key.owner, key.repo))) {
    return notFound();
  }
  return proxy(key, "GET");
}

export async function onRequestHead({ env, params, request }) {
  const key = keyFrom(params);
  if (!key || !(await isAllowed(env, request, key.owner, key.repo))) {
    return notFound();
  }
  const upstream = await proxy(key, "HEAD");
  return new Response(null, upstream);
}
