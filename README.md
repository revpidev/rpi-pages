# revpi.dev — rpi 产品端点部署（Cloudflare Pages）

rpi 的 5 个产品端点默认值自 ADR-0009 起指向 `revpi.dev`。本仓库是 rpi 的官网 / 产品端点部署仓库（Cloudflare Pages 项目，项目名 `revpi`）：静态目录内容 + Pages Functions，零后端。

> 仓库拆分（rpi/rpi-docs/rpi-pages 三个独立 git 项目）：本站为 **rpi-pages**；rpi 源码在 **rpi** 仓库，文档在 **rpi-docs** 仓库（ADR-0009 全文见 rpi-docs 的 `adr/0009-product-endpoints.md`）。
`index.html` 为站点首页（开源项目主页，含特性/快速开始/端点说明），
`/` 请求直接命中。

## 端点 → 文件映射

| 端点 | 实现 | 说明 |
|---|---|---|
| `GET /api/models/providers/{id}` | `api/models/providers/*.json`（生成） | 模型目录 overlay，`{"models":[...]}`；静态资产自动带 ETag/Last-Modified，客户端 `If-None-Match` 4h 窗口 revalidate 走 304；未收录 provider 返回 404 = "overlay 不可用"（由 `functions/api/models/providers/[id].json.js` 代理实现，SPA fallback 会兜底 index.html，函数层以 content-type 区分） |
| `GET /api/latest-version` | `api/latest-version.json`（生成） | 版本检查 `{"version","packageName","note"}`；发版时更新；无后缀路径由 `_redirects` rewrite |
| `GET /api/report-install` | `functions/api/report-install.js` | telemetry 收口，204；绑定 KV `ANALYTICS` 可记录安装统计 |
| `/session/#{gistId}` | `session/index.html` | share viewer；gist id 在 URL fragment 里（`get_share_viewer_url` 拼 `#`），页面 JS 读取并渲染 gist raw |
| `/changelog` | `changelog.html` | 更新横幅里的 changelog 链接 |

## 生成 + 部署

### 部署：Cloudflare Pages Git 集成（push 即发布）

仓库已连接到 Pages 项目 `revpi`：push 到 `main` 自动部署生产，PR 自动创建
preview 环境。构建配置（连接时在控制台填写，见下方步骤）：

| 配置项 | 值 | 说明 |
|---|---|---|
| Production branch | `main` | 生产分支 |
| Build command | 留空 | 生成产物 `api/*.json` 已提交入库；构建环境没有 `../rpi` 源码，跑生成脚本会失败 |
| Output directory | `.` | 仓库根即站点根 |

> **注意**：连接后不要再手动 `npx wrangler pages deploy` —— 手动部署与
> Git 集成互相覆盖（最后部署者赢），会造成线上与仓库不一致。
> 本地预览用 `npx wrangler pages dev .`。

控制台连接步骤（一次性）：

1. dash.cloudflare.com → Workers & Pages → 选中 `revpi` 项目
2. Settings → Builds & deployments → **Connect to Git** → 授权 GitHub
3. 选择 `revpidev/rpi-pages`，按上表填写构建配置 → Save and Deploy
4. 等待首次构建完成，访问 https://revpi.pages.dev 与 https://revpi.dev 验证

### 数据更新（发版流程）

catalog 与 latest-version 由 rpi 源码仓库生成。更新数据：

```bash
# 1. 生成 catalog 与 latest-version（rpi 源码仓库默认取同级 `../rpi`，
#    可用 --rpi-repo 覆盖；版本默认取 rpi workspace Cargo.toml）
python3 scripts/generate-site.py
#    发版时：python3 scripts/generate-site.py --version 0.2.0 --note "..."

# 2. 提交并推送 —— Git 集成自动部署
cd /home/leven/develop/ai/revpi/rpi-pages
git add api/
git commit -m "chore(api): 同步 catalog / latest-version"
git push
```

### 首次创建项目（旧流程，仅存档）

```bash
npx wrangler pages project create revpi --production-branch main
# 注意：wrangler 4.x 的 Pages Functions 目录取「当前工作目录/functions」，
# 必须在 rpi-pages 仓库根执行部署（仓库根目录部署会丢失 functions）
npx wrangler pages deploy . --project-name=revpi --branch main
# 自定义域（DNS 在 Cloudflare 托管时自动配置）：
#   Pages 控制台 → Custom domains → 添加 revpi.dev（已完成）
```

### （可选）telemetry 统计

```bash
npx wrangler kv namespace create ANALYTICS   # 把 id 填入 wrangler.toml 后重新部署
```

## 客户端侧

默认端点已在代码里改为 revpi.dev（ADR-0009）。仍可逐项覆盖：

```bash
RPI_MODEL_CATALOG_URL=https://revpi.dev        # 或 settings.json modelCatalogUrl
RPI_VERSION_CHECK_URL=https://revpi.dev/api/latest-version
RPI_TELEMETRY_URL=https://revpi.dev/api/report-install
RPI_SHARE_VIEWER_URL=https://revpi.dev/session
# 字面量 off 关闭对应端点；RPI_OFFLINE 全局关闭
```

## 兼容性注意

- **Last-Modified 必须晚于本地 builtin `generated_at`**：`remote_models`
  在 `last_modified <= local_generated_at` 时忽略 overlay（用本地数据）。
  Pages 静态资产的 Last-Modified 是部署时间，天然满足；重新部署即刷新。
- **radius 不在 catalog 内**：radius 是动态 gateway provider（默认
  `https://radius.pi.dev`，上游托管服务，非静态内容），`/api/models/providers/radius`
  返回 404 即"overlay 不可用"语义，radius 用户不受影响。
- **数据源**：catalog 由 rpi 源码仓库的 `crates/rpi-ai/src/providers/data/*.json`
  生成（`scripts/generate-site.py` 读取，默认同级 `../rpi`），与 rpi 发版同步更新。
