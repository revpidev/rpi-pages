#!/usr/bin/env python3
"""Generate the revpi.dev static site from rpi's built-in provider data.

Run from this repository root (rpi-pages):

    python3 scripts/generate-site.py

The rpi source repository is read for the built-in provider data and the
workspace version. It defaults to the sibling directory `../rpi` (the
local layout rpi/rpi-pages siblings) and can be overridden with --rpi-repo.

Output (Cloudflare Pages project rooted at this repository):

- api/models/providers/{providerId}.json — one catalog per built-in provider.
  The rpi client fetches `GET {catalogBaseUrl}/api/models/providers/{id}`
  (remote_catalog_provider.rs): the body may be a bare array,
  `{"models": [...]}` or a keyed object of Model objects carrying `id` —
  this script emits `{"models": [...]}`. The data files are nested
  `{apiKind: {modelId: Model}}`, so the model objects are flattened.
- api/latest-version.json — `{"version","packageName","note"}` consumed by
  the version check (version_check.rs: `version` is required, non-2xx or a
  missing version means "no update"). Defaults to the rpi workspace version;
  override with --version when publishing a release.
- install.sh / install.ps1 — the installer scripts served at the site root
  (`https://revpi.dev/install.sh`, `.../install.ps1`). The single source of
  truth lives in the rpi repository; this step copies them verbatim.
- api/extensions/index.json, api/extensions/<name>.json,
  api/extensions/allowlist.json — the extension registry API
  (extension-distribution.md §5.2). Entries are read from `registry/*.json`
  in this repository (transitional home until the revpidev/rpi-plugins index
  repository exists); the version matrix is enumerated from each entry's
  GitHub repository releases (`.rpix` assets + `.sha256` sidecars).
  `GITHUB_TOKEN` is optional (anonymous requests are rate-limited); if the
  API is unreachable the affected plugin degrades to `versions: []` with a
  warning instead of failing the whole run.

Static assets get ETag / Last-Modified from Cloudflare Pages automatically,
so the client's If-None-Match revalidation (4h window) works as-is.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # rpi-pages repository root
DEFAULT_RPI_REPO = SITE.parent / "rpi"  # sibling rpi source repository

PACKAGE_NAME = "rpi"


def workspace_version(rpi_repo: Path) -> str:
    text = (rpi_repo / "Cargo.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("workspace version not found in rpi Cargo.toml")
    return match.group(1)


def generate_catalogs(rpi_repo: Path) -> list[str]:
    data_dir = rpi_repo / "crates/rpi-ai/src/providers/data"
    out_dir = SITE / "api/models/providers"
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for data_file in sorted(data_dir.glob("*.json")):
        if data_file.name.startswith("."):
            continue  # .manifest.json — the catalog manifest, not a provider
        provider_id = data_file.stem  # kebab-case, matches the client's encodeURIComponent'd id
        payload = json.loads(data_file.read_text(encoding="utf-8"))
        models = []
        for api_models in payload.values():  # api kind -> {modelId: Model}
            models.extend(api_models.values())
        (out_dir / f"{provider_id}.json").write_text(
            json.dumps({"models": models}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        generated.append(provider_id)
    return generated


def generate_latest_version(version: str, note: str | None) -> None:
    out = SITE / "api/latest-version.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": version, "packageName": PACKAGE_NAME}
    if note:
        payload["note"] = note
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


INSTALL_SCRIPTS = ["install.sh", "install.ps1"]


def sync_install_scripts(rpi_repo: Path) -> list[str]:
    """安装脚本以静态文件放站点根（/install.sh、/install.ps1）；
    单一事实源在 rpi 源码仓库，这里原样拷贝。"""
    copied = []
    for name in INSTALL_SCRIPTS:
        src = rpi_repo / name
        if not src.is_file():
            raise SystemExit(f"install script not found in rpi repo: {src}")
        (SITE / name).write_bytes(src.read_bytes())
        copied.append(name)
    return copied


# ---------------------------------------------------------------------------
# Extensions registry（设计文档 extension-distribution.md §5/§6）
#
# registry/ 目录是索引仓库 revpidev/rpi-plugins 建立前的过渡位置：每个插件
# 一条 <name>.json（name/repository/description/author/license，第一方附
# "official": true，可选 "yankedVersions": [...] 覆盖、"lockstepHost": true
# 表示与宿主锁步发布、每个版本 minHostVersion = 该版本自身）。版本矩阵不由
# 作者手填——这里枚举各 repository 的 GitHub Release，按
# `<name>-<version>[-<target>].rpix` 精确匹配资产，从同 Release 的
# `<file>.sha256` sidecar（coreutils 格式 `<hex>  <basename>`）采信 sha256。
# ---------------------------------------------------------------------------

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")
SHA256_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\S+$")


def load_registry() -> list[dict]:
    registry_dir = SITE / "registry"
    entries = []
    for path in sorted(registry_dir.glob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        name = entry.get("name", "")
        if not NAME_PATTERN.match(name):
            raise SystemExit(f"registry/{path.name}: invalid extension name {name!r}")
        if path.stem != name:
            raise SystemExit(f"registry/{path.name}: file name must match name {name!r}")
        if not re.match(r"^[\w.-]+/[\w.-]+$", entry.get("repository", "")):
            raise SystemExit(f"registry/{path.name}: invalid repository {entry.get('repository')!r}")
        for field in ("description", "author", "license"):
            if not entry.get(field):
                raise SystemExit(f"registry/{path.name}: missing required field {field!r}")
        entries.append(entry)
    return entries


def load_extension_manifests(rpi_repo: Path) -> dict[str, dict]:
    """第一方插件的 kind/capabilities/rpiAbi 等以各 crate 根的
    rpi-extension.json 为准（crate 与 manifest 同仓库同版本）。"""
    manifests = {}
    for path in sorted(rpi_repo.glob("crates/*/rpi-extension.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("name"):
            manifests[manifest["name"]] = manifest
    return manifests


def github_api(url: str):
    """GitHub API 请求；GITHUB_TOKEN 可选（匿名有 60 次/时限流）。"""
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "rpi-pages generate-site")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def semver_key(version: str):
    """纯 stdlib 的 semver 排序键（release > prerelease，数值段按数值比）。"""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", version)
    if not match:
        raise SystemExit(f"invalid semver version: {version!r}")
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    pre = match.group(4)
    if pre is None:
        return (major, minor, patch, 1, ())
    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split("."))
    return (major, minor, patch, 0, parts)


def release_assets(repository: str) -> list[dict] | None:
    """枚举仓库全部 Release；失败（网络/限流/仓库不存在）返回 None，
    调用方降级为 versions: [] 并警告，不让整个生成失败。"""
    url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
    try:
        return json.loads(github_api(url))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"warning: cannot enumerate releases for {repository}: {exc}", file=sys.stderr)
        return None


def artifact_sha256(assets_by_name: dict, file_name: str) -> str | None:
    """从同 Release 的 <file>.sha256 sidecar 内容解析 sha256。"""
    sidecar = assets_by_name.get(f"{file_name}.sha256")
    if not sidecar:
        return None
    try:
        text = github_api(sidecar["browser_download_url"]).decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        print(f"warning: cannot fetch {file_name}.sha256: {exc}", file=sys.stderr)
        return None
    match = SHA256_LINE.match(text.strip())
    return match.group(1).lower() if match else None


def extension_versions(entry: dict) -> list[dict] | None:
    """按 §3.2 命名规则匹配 .rpix 资产，生成版本矩阵（semver 降序）。
    返回 None 表示 releases 枚举失败（降级路径）。"""
    name = entry["name"]
    releases = release_assets(entry["repository"])
    if releases is None:
        return None
    versions = {}
    for release in releases:
        if release.get("draft"):
            continue
        tag_match = TAG_PATTERN.match(release.get("tag_name", ""))
        if not tag_match:
            continue  # 只认 v<semver> 形态的 tag
        version = tag_match.group(1)
        assets_by_name = {a["name"]: a for a in release.get("assets", [])}
        artifacts = []
        for asset_name in sorted(assets_by_name):
            if not asset_name.endswith(".rpix"):
                continue  # 本体 rpi-* 资产无 .rpix 后缀，天然不串扰（§9.3）
            prefix = f"{name}-{version}"
            if asset_name == f"{prefix}.rpix":
                target = None  # wasm 载体：单 artifact 全平台通用
            elif asset_name.startswith(f"{prefix}-") and asset_name.endswith(".rpix"):
                target = asset_name[len(prefix) + 1 : -len(".rpix")]
            else:
                continue  # 别的插件的资产
            sha256 = artifact_sha256(assets_by_name, asset_name)
            if not sha256:
                print(f"warning: {asset_name}: missing/invalid .sha256 sidecar, skipped", file=sys.stderr)
                continue
            artifacts.append(
                {"target": target, "file": asset_name, "sha256": sha256, "release": release["tag_name"]}
            )
        if artifacts:
            artifacts.sort(key=lambda a: (a["target"] or "", a["file"]))
            versions[version] = artifacts
    yanked = set(entry.get("yankedVersions", []))
    ordered = sorted(versions, key=semver_key, reverse=True)
    return [{"version": v, "yanked": v in yanked, "artifacts": versions[v]} for v in ordered]


def generate_extensions(rpi_repo: Path) -> list[str]:
    """产出 api/extensions/{index,allowlist,<name>}.json，schema 见设计 §5.2。"""
    entries = load_registry()
    manifests = load_extension_manifests(rpi_repo)
    out_dir = SITE / "api/extensions"
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    index = []
    names = []
    for entry in entries:
        name = entry["name"]
        names.append(name)
        manifest = manifests.get(name, {})
        kind = "native" if manifest.get("native") else ("wasm" if manifest.get("wasm") else entry.get("kind"))
        capabilities = manifest.get("capabilities", entry.get("capabilities", []))
        rpi_abi = manifest.get("rpiAbi", entry.get("rpiAbi"))

        versions = extension_versions(entry)
        if versions is None:  # 降级路径：枚举失败不让整个生成失败
            print(f"warning: {name}: releases unavailable, emitting versions: []", file=sys.stderr)
            versions = []
        # minHostVersion：条目/manifest 的显式值优先；lockstepHost 表示插件与
        # 宿主锁步发布（CI 打包时注入 minHostVersion = 自身版本，源码树 manifest
        # 无此字段），这里按同一语义回填每个版本，CLI 兼容预检（§7.2）才能命中。
        min_host = manifest.get("minHostVersion", entry.get("minHostVersion"))
        lockstep = entry.get("lockstepHost", False)
        for record in versions:
            # 版本级元数据当前与 manifest 同源（第一方与本体锁步，§9.1）；
            # 字段顺序固定：version/rpiAbi/minHostVersion/capabilities/yanked/artifacts
            if rpi_abi is not None:
                record["rpiAbi"] = rpi_abi
            if min_host:
                record["minHostVersion"] = min_host
            elif lockstep:
                record["minHostVersion"] = record["version"]
            record["capabilities"] = capabilities
        versions = [
            {key: record[key] for key in ("version", "rpiAbi", "minHostVersion", "capabilities", "yanked", "artifacts") if key in record}
            for record in versions
        ]

        latest = next((r["version"] for r in versions if not r["yanked"]), None)
        detail = {
            "schemaVersion": 1,
            "name": name,
            "repository": entry["repository"],
            "author": entry["author"],
        }
        if entry.get("homepage"):
            detail["homepage"] = entry["homepage"]
        detail["license"] = entry["license"]
        if kind:
            detail["kind"] = kind
        if entry.get("official"):
            detail["official"] = True
        detail["versions"] = versions
        (out_dir / f"{name}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        index_entry = {
            "name": name,
            "latest": latest,
            "description": entry["description"],
            "kind": kind,
            "capabilities": capabilities,
            "downloads": None,
        }
        if entry.get("official"):
            index_entry["official"] = True
        index.append(index_entry)

    (out_dir / "index.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "generatedAt": generated_at, "extensions": index},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # 下载代理（functions/extensions/download/[[path]].js）的 repo 白名单：
    # 索引全量 repo 集合，不在名单的一律 404，防止代理变成开放转发器。
    repositories = sorted({entry["repository"] for entry in entries})
    (out_dir / "allowlist.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "generatedAt": generated_at, "repositories": repositories},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rpi-repo",
        default=str(DEFAULT_RPI_REPO),
        help=f"path to the rpi source repository (default: {DEFAULT_RPI_REPO})",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="published version for api/latest-version.json (default: rpi workspace Cargo.toml version)",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="optional release note shown by the client's update banner",
    )
    args = parser.parse_args()

    rpi_repo = Path(args.rpi_repo).resolve()
    if not (rpi_repo / "Cargo.toml").is_file():
        raise SystemExit(f"rpi source repository not found at {rpi_repo}")

    generated = generate_catalogs(rpi_repo)
    version = args.version or workspace_version(rpi_repo)
    generate_latest_version(version, args.note)
    copied = sync_install_scripts(rpi_repo)
    extensions = generate_extensions(rpi_repo)
    print(f"catalogs: {len(generated)} providers under {SITE / 'api/models/providers'}")
    print(f"version:  api/latest-version.json -> v{version}")
    print(f"install:  {', '.join(copied)} synced to site root")
    print(f"extensions: {len(extensions)} plugins under {SITE / 'api/extensions'} "
          f"({', '.join(extensions)})")
    print("deploy:   npx wrangler pages deploy . --project-name=revpi --branch main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
