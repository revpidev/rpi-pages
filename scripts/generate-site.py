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

Static assets get ETag / Last-Modified from Cloudflare Pages automatically,
so the client's If-None-Match revalidation (4h window) works as-is.
"""
import argparse
import json
import re
import sys
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
    print(f"catalogs: {len(generated)} providers under {SITE / 'api/models/providers'}")
    print(f"version:  api/latest-version.json -> v{version}")
    print(f"install:  {', '.join(copied)} synced to site root")
    print("deploy:   npx wrangler pages deploy . --project-name=revpi --branch main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
