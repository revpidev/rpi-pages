// registry/ 目录是索引源数据（生成 api/extensions/* 的输入），属仓库内部
// 文件不对外提供——同 scripts/ 的处理（direct upload 下 .assetsignore 与
// _redirects-404 均不生效，用路由级函数直接 404）。
export async function onRequest() {
  return new Response(null, { status: 404 });
}
