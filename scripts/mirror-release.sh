#!/bin/sh
# 把 rpi 的 GitHub Release 资产镜像到官网 R2 桶（revpi.dev/releases/download/...）。
#
# 维护者发版时手动执行（GitHub Release 的 12 个资产齐全之后）：
#   sh scripts/mirror-release.sh 0.1.0        # 版本号不带 v 前缀
#
# 前置条件：
#   - 已 `npx wrangler login`（写 R2 需要账号凭据）
#   - 桶已一次性创建：npx wrangler r2 bucket create rpi-releases
#   - curl 可用
#
# 契约（与 functions/releases/download/[[path]].js 一致）：
#   R2 key = `v<version>/<filename>`；资产命名见 rpi 仓库
#   .github/workflows/build.yml —— 6 目标 × (资产 + .sha256 sidecar) = 12 个文件，
#   unix 目标为 .tar.gz，windows 目标为 .zip。
set -eu

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "usage: sh scripts/mirror-release.sh <version>   # 如 0.1.0（不带 v 前缀）" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "error: 需要 curl，但未找到" >&2
  exit 1
fi

BUCKET="rpi-releases"
TAG="v${VERSION}"
BASE="https://github.com/revpidev/rpi/releases/download/${TAG}"

# 目标清单与 rpi 仓库 .github/workflows/build.yml 的 matrix 保持一致
UNIX_TARGETS="aarch64-apple-darwin x86_64-unknown-linux-gnu x86_64-unknown-linux-musl aarch64-unknown-linux-musl aarch64-unknown-linux-gnu"
WINDOWS_TARGETS="x86_64-pc-windows-msvc"

FILES=""
for target in $UNIX_TARGETS; do
  FILES="$FILES rpi-${VERSION}-${target}.tar.gz rpi-${VERSION}-${target}.tar.gz.sha256"
done
for target in $WINDOWS_TARGETS; do
  FILES="$FILES rpi-${VERSION}-${target}.zip rpi-${VERSION}-${target}.zip.sha256"
done

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# 任一资产下载失败（curl -f 遇非 2xx 即报错）则整个脚本退出，不上传半成品
for file in $FILES; do
  echo "download: ${BASE}/${file}"
  curl -fSL --retry 3 -o "${TMP_DIR}/${file}" "${BASE}/${file}"
done

for file in $FILES; do
  echo "upload:   ${BUCKET}/${TAG}/${file}"
  npx wrangler r2 object put "${BUCKET}/${TAG}/${file}" --file="${TMP_DIR}/${file}" --remote
done

# 取第一个资产名用于验证提示（set -- 按空格分词，POSIX 安全：文件名无空格）
set -- $FILES
echo ""
echo "完成：12 个资产已上传到 R2 桶 ${BUCKET}（key 前缀 ${TAG}/）。"
echo "请访问 https://revpi.dev/releases/download/${TAG}/$1 验证。"
