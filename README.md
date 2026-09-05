# revpi.dev — rpi 产品端点部署（Cloudflare Pages）

rpi 的 5 个产品端点默认值自 ADR-0009 起指向 `revpi.dev`。本仓库是 rpi 的官网 / 产品端点部署仓库（Cloudflare Pages 项目，项目名 `revpi`）：静态目录内容 + Pages Functions，零后端。

> 仓库拆分（rpi 源码仓库与本部署仓库独立；需求 / 设计 / ADR 等工程文档维护在私有文档仓库，未随仓库公开）：本站为 **rpi-pages**；rpi 源码在 **rpi** 仓库，端点契约与部署说明见本 README。`index.html` 为站点首页（开源项目主页，含特性/快速开始/端点说明），`/` 请求直接命中。

## 端点 → 文件映射

| 端点 | 实现 | 说明 |
|---|---|---|
| `GET /api/models/providers/{id}` | `api/models/providers/*.json`（生成） | 模型目录 overlay，`{"models":[...]}`；静态资产自动带 ETag/Last-Modified，客户端 `If-None-Match` 4h 窗口 revalidate 走 304；未收录 provider 返回 404 = "overlay 不可用"（由 `functions/api/models/providers/[id].json.js` 代理实现，SPA fallback 会兜底 index.html，函数层以 content-type 区分） |
| `GET /api/latest-version` | `api/latest-version.json`（生成） | 版本检查 `{"version","packageName","note"}`；发版时更新；无后缀路径由 `_redirects` rewrite |
| `GET /api/report-install` | `functions/api/report-install.js` | telemetry 收口，204；绑定 KV `ANALYTICS` 可记录安装统计 |
| `GET /install.sh`、`GET /install.ps1` | `install.sh`、`install.ps1`（静态文件，站点根） | 安装脚本；单一事实源在 rpi 仓库，由 `scripts/generate-site.py` 同步拷贝 |
| `GET /releases/download/v{ver}/{file}` | `functions/releases/download/[[path]].js` | GitHub Release 资产官网代理（URL 与 GitHub 同形只换 base，国内不可直连 GitHub 时由边缘节点回源转发）；key 形状严格校验，未命中/上游 404 均 404；零存储，发版零额外步骤 |
| `GET /api/extensions/index.json` | `api/extensions/index.json`（生成） | 扩展插件索引（目录页与 `rpi list` 用），schema 见 extension-distribution §5.2；由 `registry/*.json` 条目 + 各仓库 GitHub Releases 枚举生成 |
| `GET /api/extensions/{name}.json` | `api/extensions/<name>.json`（生成）+ `functions/api/extensions/[name].json.js` | 单插件版本矩阵（`rpi install/info` 的版本解析用）；未收录 name 返回裸 404（Function 层防 SPA fallback，同 providers 模式） |
| `GET /api/extensions/allowlist.json` | `api/extensions/allowlist.json`（生成） | 下载代理的 repo 白名单（索引全量 repo 集合），`functions/extensions/download` 校验用 |
| `GET /extensions/download/{owner}/{repo}/{tag}/{file}` | `functions/extensions/download/[[path]].js` | 插件 `.rpix` 资产官网代理；owner/repo 必须在 allowlist 内（否则 404，防开放转发器），tag 严格 `v<semver>`、file 严格 `*.rpix[.sha256]`；回源 GitHub 流式转发，上游 404→404，immutable 缓存 |
| `/extensions` | `extensions.html` | 插件目录页：客户端读 index.json 渲染卡片（official 徽标、capabilities、L0 信任提示、安装命令复制） |
| `/session/#{gistId}` | `session/index.html` | share viewer；gist id 在 URL fragment 里（`get_share_viewer_url` 拼 `#`），页面 JS 读取并渲染 gist raw |
| `/changelog` | `changelog.html` | 更新横幅里的 changelog 链接 |

## 生成 + 部署

### 部署：Cloudflare Pages Git 集成（push 即发布）

仓库已连接到 Pages 项目 `revpi`：push 到 `main` 自动部署生产，PR 自动创建 preview 环境。构建配置（连接时在控制台填写，见下方步骤）：

| 配置项 | 值 | 说明 |
|---|---|---|
| Production branch | `main` | 生产分支 |
| Build command | 留空 | 生成产物 `api/*.json` 已提交入库；构建环境没有 `../rpi` 源码，跑生成脚本会失败 |
| Output directory | `.` | 仓库根即站点根 |

> **注意**：连接后不要再手动 `npx wrangler pages deploy` —— 手动部署与 Git 集成互相覆盖（最后部署者赢），会造成线上与仓库不一致。本地预览用 `npx wrangler pages dev .`。

控制台连接步骤（一次性）：

1. dash.cloudflare.com → Workers & Pages → 选中 `revpi` 项目
2. Settings → Builds & deployments → **Connect to Git** → 授权 GitHub
3. 选择 `revpidev/rpi-pages`，按上表填写构建配置 → Save and Deploy
4. 等待首次构建完成，访问 https://revpi.pages.dev 与 https://revpi.dev 验证

### 数据更新（发版流程）

catalog 与 latest-version 由 rpi 源码仓库生成。更新数据：

```bash
# 1. 生成 catalog 与 latest-version，并同步安装脚本 install.sh/install.ps1
#    到站点根（rpi 源码仓库默认取同级 `../rpi`，可用 --rpi-repo 覆盖；
#    版本默认取 rpi workspace Cargo.toml）
python3 scripts/generate-site.py
#    发版时：python3 scripts/generate-site.py --version 0.2.0 --note "..."

# 2. 提交并推送 —— Git 集成自动部署
cd /home/leven/develop/ai/revpi/rpi-pages
git add api/ install.sh install.ps1
git commit -m "chore(api): 同步 catalog / latest-version"
git push
```

### 扩展插件 registry（`registry/`）

`registry/<name>.json` 是扩展索引的**源数据**（每插件一条：`name/repository/description/author/license`，第一方附 `"official": true`，可选 `"yankedVersions": [...]` 做 yank 覆盖、`"lockstepHost": true` 表示插件与宿主锁步发布——生成时每个版本的 `minHostVersion` 回填为该版本自身，条目里的显式 `minHostVersion` 优先；对应 CI 打包时向 .rpix manifest 注入同值的语义）。**过渡说明**：设计（extension-distribution §5.1）中索引归属独立仓库 `revpidev/rpi-plugins`，该仓库尚未建立，本期先把索引数据放在本仓库 `registry/`；独立仓库建立后迁出，生成脚本改为读该仓库。

`generate-site.py` 读 `registry/*.json`，对每个 `repository` 调 GitHub Releases API 枚举版本矩阵（按 `<name>-<version>[-<target>].rpix` 精确匹配资产，sha256 采信同 Release 的 `<file>.sha256` sidecar），产出 `api/extensions/{index,allowlist,<name>}.json`——commit 即部署，与 catalog 同管线。`GITHUB_TOKEN` 环境变量可选（匿名 60 次/时限流）；API 不可达时对应插件降级为 `versions: []` 并警告，不让整个生成失败。第一方插件的 description/capabilities/kind 采信 `../rpi` 各 crate 根的 `rpi-extension.json`。

> Release 资产镜像（`/releases/download/*`）是 Function 代理回源 GitHub，零存储、发版零额外步骤——GitHub Release 资产发布即可用，无需上传。

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

- **Last-Modified 必须晚于本地 builtin `generated_at`**：`remote_models` 在 `last_modified <= local_generated_at` 时忽略 overlay（用本地数据）。Pages 静态资产的 Last-Modified 是部署时间，天然满足；重新部署即刷新。
- **radius 不在 catalog 内**：radius 是动态 gateway provider（默认 `https://radius.pi.dev`，上游托管服务，非静态内容），`/api/models/providers/radius` 返回 404 即"overlay 不可用"语义，radius 用户不受影响。
- **数据源**：catalog 由 rpi 源码仓库的 `crates/rpi-ai/src/providers/data/*.json` 生成（`scripts/generate-site.py` 读取，默认同级 `../rpi`），与 rpi 发版同步更新。
