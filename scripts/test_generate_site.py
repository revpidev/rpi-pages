#!/usr/bin/env python3
"""generate-site.py 生成侧回归（V14-19 RC 更新通道，rpi 自有）。

stdlib unittest，零第三方依赖；`python3 scripts/test_generate_site.py` 直接跑。
覆盖（任务文档 V14-19 FR-G / §4.2）：
- RC 版本号形态判定（R6.1.1：小写 rc、点分数字段、禁前导零）；
- 预发布形态闸：非 rc 形态的预发布 tag 告警并拒入索引（R6.5.3）；
- `latest` 字段排除预发布（rc 不顶掉 stable 展示位）；
- `--rc-version` 写 RC 端点（合法形态校验 + payload schema）；
- semver 排序键的预发布段（rc.10 > rc.9；rc < stable）；
- TE47 §8-3：`github_api` 网络抖动重试（URL/OSError）与最终失败降级；
- V18-13 `--publish-profiles`：12 个 standard/full 归档的扁平索引（顺序 /
  排序 / 排除 mini、static、.rpix）、缺件/坏侧车/版本不符失败且不落盘、
  无旗标不触碰 `api/install-profiles/`。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent / "generate-site.py"

_spec = importlib.util.spec_from_file_location("generate_site", SCRIPT)
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)


def _release(tag: str, assets: dict[str, str] | None = None) -> dict:
    """构造 GitHub Release API 条目；assets: name -> sha256（sidecar 由
    artifact_sha256 读取，测试里直接 stub 掉）。"""
    return {
        "tag_name": tag,
        "draft": False,
        "assets": [
            {"name": name, "browser_download_url": f"https://x/{name}.sha256"}
            for name in (assets or {})
        ],
    }


class RcShapeTests(unittest.TestCase):
    """R6.1.1 形态判定：`<stable>-rc.<N>`。"""

    def test_rc_shape_matrix(self):
        for ok in ["0.1.5-rc.1", "0.1.5-rc.2", "1.0.0-rc.10", "0.1.5-rc.0"]:
            self.assertTrue(gs.is_rc_version(ok), ok)
        for bad in [
            "0.1.5",  # stable 不是 rc
            "0.1.5-rc.01",  # 前导零（SemVer §9 禁）
            "0.1.5-rc1",  # 无点（ASCII 序排序坑 rc10 < rc9）
            "0.1.5-RC.1",  # 大写（build.yml -rc 判定 + 约定小写）
            "0.1.5-beta.1",  # 非 rc 标识
            "0.1.5-rc.1.2",  # 多段
            "not-a-version",
        ]:
            self.assertFalse(gs.is_rc_version(bad), bad)

    def test_prerelease_detection(self):
        self.assertTrue(gs.is_prerelease_version("0.1.5-rc.1"))
        self.assertTrue(gs.is_prerelease_version("0.1.5-beta.1"))
        self.assertFalse(gs.is_prerelease_version("0.1.5"))
        self.assertFalse(gs.is_prerelease_version("junk"))

    def test_semver_key_orders_rc_below_stable_and_numerically(self):
        order = [
            "0.1.4",
            "0.1.5-rc.9",
            "0.1.5-rc.10",
            "0.1.5-rc.11",
            "0.1.5",
            "0.1.6-rc.1",
        ]
        keys = [gs.semver_key(v) for v in order]
        self.assertEqual(keys, sorted(keys), "semver 全序：rc.9 < rc.10 < rc.11 < stable")


class ExtensionVersionsChannelTests(unittest.TestCase):
    """extension_versions 的形态闸与矩阵（mock releases）。"""

    def _versions(self, releases: list[dict]):
        entry = {"name": "rpi-statusline", "repository": "revpidev/rpi"}
        with mock.patch.object(gs, "release_assets", return_value=releases), mock.patch.object(
            gs, "artifact_sha256", return_value="a" * 64
        ):
            return gs.extension_versions(entry)

    def test_rc_enters_matrix_and_shape_gate_skips_non_rc_prerelease(self):
        versions = self._versions(
            [
                _release("v0.1.4", {"rpi-statusline-0.1.4.rpix": "x"}),
                _release("v0.1.5-rc.1", {"rpi-statusline-0.1.5-rc.1.rpix": "x"}),
                _release("v0.1.5-rc.02", {"rpi-statusline-0.1.5-rc.02.rpix": "x"}),
                _release("v0.1.5-beta.1", {"rpi-statusline-0.1.5-beta.1.rpix": "x"}),
                _release("v0.1.5-rc3", {"rpi-statusline-0.1.5-rc3.rpix": "x"}),
            ]
        )
        listed = [v["version"] for v in versions]
        # 合法 rc 入矩阵（供 `--rc` 通道解析）；非 rc 形态预发布被闸拦下。
        self.assertEqual(listed, ["0.1.5-rc.1", "0.1.4"])

    def test_latest_excludes_prerelease(self):
        # generate_extensions 的 latest 语义：首个非 yanked 且非预发布。
        versions = [
            {"version": "0.1.5-rc.1", "yanked": False},
            {"version": "0.1.4", "yanked": False},
        ]
        latest = next(
            (
                r["version"]
                for r in versions
                if not r["yanked"] and not gs.is_prerelease_version(r["version"])
            ),
            None,
        )
        self.assertEqual(latest, "0.1.4")
        # 全预发布索引（极端）→ latest 为 None，不回退到 rc。
        versions = [{"version": "0.1.5-rc.1", "yanked": False}]
        latest = next(
            (
                r["version"]
                for r in versions
                if not r["yanked"] and not gs.is_prerelease_version(r["version"])
            ),
            None,
        )
        self.assertIsNone(latest)


class RcEndpointTests(unittest.TestCase):
    """--rc-version 写 RC 端点（FR-G R1）。"""

    def test_writes_payload_and_rejects_bad_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gs, "SITE", Path(tmp)):
                gs.generate_latest_rc_version("0.1.5-rc.1", "preview build")
                payload = json.loads(
                    (Path(tmp) / "api/latest-rc-version.json").read_text(encoding="utf-8")
                )
            self.assertEqual(
                payload, {"version": "0.1.5-rc.1", "packageName": "rpi", "note": "preview build"}
            )
            # 非 rc 形态在发布侧直接拒绝（防手滑把 stable/错形写进 RC 端点）。
            with mock.patch.object(gs, "SITE", Path(tmp)):
                with self.assertRaises(SystemExit):
                    gs.generate_latest_rc_version("0.1.5", None)
                with self.assertRaises(SystemExit):
                    gs.generate_latest_rc_version("0.1.5-rc.01", None)

    def test_stable_endpoint_untouched_by_rc_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gs, "SITE", Path(tmp)):
                gs.generate_latest_rc_version("0.1.5-rc.1", None)
                self.assertFalse((Path(tmp) / "api/latest-version.json").exists())


class StableEndpointGuardTests(unittest.TestCase):
    """stable 端点预发布形态闸（rpi-pages#4）。"""

    def test_refuses_prerelease_version(self):
        # RC 窗口内裸跑（workspace 回落 0.1.4-rc.4）与手滑 --version 预发布
        # 同样拒绝——stable 端点不得携带预发布（R6.2.4，防 4160d1e 事故重演）。
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gs, "SITE", Path(tmp)):
                with self.assertRaises(SystemExit) as ctx:
                    gs.generate_latest_version("0.1.4-rc.4", None)
                self.assertIn("prerelease", str(ctx.exception))
                with self.assertRaises(SystemExit):
                    gs.generate_latest_version("0.1.5-beta.1", None)
                self.assertFalse((Path(tmp) / "api/latest-version.json").exists())

    def test_allow_prerelease_escape_hatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gs, "SITE", Path(tmp)):
                gs.generate_latest_version("0.1.4-rc.4", None, allow_prerelease=True)
                payload = json.loads(
                    (Path(tmp) / "api/latest-version.json").read_text(encoding="utf-8")
                )
            self.assertEqual(payload["version"], "0.1.4-rc.4")

    def test_stable_version_passes_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gs, "SITE", Path(tmp)):
                gs.generate_latest_version("0.1.4", "hello")
                payload = json.loads(
                    (Path(tmp) / "api/latest-version.json").read_text(encoding="utf-8")
                )
            self.assertEqual(
                payload, {"version": "0.1.4", "packageName": "rpi", "note": "hello"}
            )

    def test_main_action_matrix(self):
        # stable_endpoint_action 决策矩阵（rpi-pages#4）：
        # - 稳定版本 → write（幂等重写）
        # - RC-only（--rc-version、无 --version）+ 预发布 workspace → skip
        #   （RELEASING.md RC 流程不碰 stable 端点）
        # - 裸跑 + 预发布 workspace → refuse（事故闸）
        # - 显式预发布 --version → refuse（手滑闸，含 RC-only 时）
        self.assertEqual(gs.stable_endpoint_action(None, None, "0.1.3"), "write")
        self.assertEqual(gs.stable_endpoint_action("0.1.3", None, "0.1.3"), "write")
        self.assertEqual(
            gs.stable_endpoint_action(None, "0.1.4-rc.5", "0.1.4-rc.5"), "skip"
        )
        self.assertEqual(gs.stable_endpoint_action(None, None, "0.1.4-rc.5"), "refuse")
        self.assertEqual(
            gs.stable_endpoint_action("0.1.4-rc.5", "0.1.4-rc.5", "0.1.4-rc.5"),
            "refuse",
        )
        self.assertEqual(gs.stable_endpoint_action("0.1.4-rc.5", None, "0.1.4-rc.5"), "refuse")


class OfficialNamingTests(unittest.TestCase):
    """R7.1.1/R7.1.3（ADR-0033，TE42）：`rpi-` 前缀官方保留 + 索引层拒绝。"""

    def _entry(self, **overrides):
        entry = {
            "name": "rpi-todo",
            "repository": "revpidev/rpi",
            "description": "d",
            "author": "revpidev",
            "license": "MIT",
            "official": True,
        }
        entry.update(overrides)
        return entry

    def test_prefix_requires_official_and_allowlisted_repository(self):
        # 正例：official + 官方 allowlist repository。
        gs.validate_official_naming("rpi-todo.json", self._entry())
        # 负例：第三方声称 rpi- 前缀 → 拒绝。
        with self.assertRaises(SystemExit):
            gs.validate_official_naming("rpi-fake.json", self._entry(official=False))
        # 负例：official 但 repository 不在官方 allowlist → 拒绝。
        with self.assertRaises(SystemExit):
            gs.validate_official_naming(
                "rpi-fake.json", self._entry(repository="evil/rpi-todo")
            )
        # 负例：official 条目必须使用 rpi- 前缀（R7.1.1 反向）。
        with self.assertRaises(SystemExit):
            gs.validate_official_naming("todo.json", self._entry(name="todo"))
        # 第三方（非 official）非 rpi- 前缀不受约束。
        gs.validate_official_naming(
            "acme-todo.json", self._entry(name="acme-todo", official=False, repository="acme/x")
        )

    def test_load_registry_rejects_third_party_official_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry"
            registry.mkdir()
            (registry / "rpi-fake.json").write_text(
                json.dumps(
                    {
                        "name": "rpi-fake",
                        "repository": "acme/rpi-fake",
                        "description": "d",
                        "author": "acme",
                        "license": "MIT",
                        "official": False,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(gs, "SITE", Path(tmp)):
                with self.assertRaises(SystemExit):
                    gs.load_registry()


class RenamedIndexTests(unittest.TestCase):
    """TE42：索引换键 + 详情文件名/内容由生成器产出（不手编；无旧名匹配表）。"""

    def test_generate_extensions_rekeys_index_and_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            registry = site / "registry"
            registry.mkdir()
            (registry / "rpi-todo.json").write_text(
                json.dumps(
                    {
                        "name": "rpi-todo",
                        "repository": "revpidev/rpi",
                        "description": "d",
                        "author": "revpidev",
                        "license": "MIT",
                        "official": True,
                        "lockstepHost": True,
                    }
                ),
                encoding="utf-8",
            )
            rpi_repo = site / "rpi"
            crate = rpi_repo / "crates" / "rpi-ext-todo"
            crate.mkdir(parents=True)
            (crate / "rpi-extension.json").write_text(
                json.dumps(
                    {
                        "name": "rpi-todo",
                        "native": "librpi_ext_todo.so",
                        "capabilities": ["tools"],
                        "rpiAbi": 1,
                    }
                ),
                encoding="utf-8",
            )
            # TE42: renamed releases carry rpi- asset names (renamed in place).
            releases = [_release("v0.1.5", {"rpi-todo-0.1.5.rpix": "x"})]
            with mock.patch.object(gs, "SITE", site), mock.patch.object(
                gs, "release_assets", return_value=releases
            ), mock.patch.object(gs, "artifact_sha256", return_value="b" * 64):
                names = gs.generate_extensions(rpi_repo)
            self.assertEqual(names, ["rpi-todo"])
            index = json.loads((site / "api/extensions/index.json").read_text(encoding="utf-8"))
            self.assertEqual([e["name"] for e in index["extensions"]], ["rpi-todo"])
            detail = json.loads((site / "api/extensions/rpi-todo.json").read_text(encoding="utf-8"))
            self.assertEqual(detail["name"], "rpi-todo")
            self.assertEqual(detail["versions"][0]["artifacts"][0]["file"], "rpi-todo-0.1.5.rpix")
            self.assertEqual(detail["versions"][0]["minHostVersion"], "0.1.5")
            allow = json.loads((site / "api/extensions/allowlist.json").read_text(encoding="utf-8"))
            self.assertEqual(allow["repositories"], ["revpidev/rpi"])

    def test_generate_extensions_carries_deprecated_marker(self):
        # v0.1.6: the index/detail payloads carry the registry entry's
        # deprecation marker (built-in MCP retired rpi-mcp-adapter). The
        # adapter entry itself was removed with ADR-0037 (TE49 tail); this
        # fixture stays as a synthetic sample so the marker path keeps a
        # regression test (same precedent as the deprecated-extension
        # runtime fixtures).
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            registry = site / "registry"
            registry.mkdir()
            (registry / "rpi-mcp-adapter.json").write_text(
                json.dumps(
                    {
                        "name": "rpi-mcp-adapter",
                        "repository": "revpidev/rpi",
                        "description": "d",
                        "author": "revpidev",
                        "license": "MIT",
                        "official": True,
                        "lockstepHost": True,
                        "deprecated": True,
                        "deprecatedMessage": "MCP is built in.",
                        "deprecatedMessageZh": "MCP 已内置。",
                    }
                ),
                encoding="utf-8",
            )
            rpi_repo = site / "rpi"
            crate = rpi_repo / "crates" / "rpi-ext-mcp-adapter"
            crate.mkdir(parents=True)
            (crate / "rpi-extension.json").write_text(
                json.dumps(
                    {
                        "name": "rpi-mcp-adapter",
                        "native": "librpi_ext_mcp_adapter.so",
                        "capabilities": ["tools"],
                        "rpiAbi": 1,
                    }
                ),
                encoding="utf-8",
            )
            releases = [_release("v0.1.5", {"rpi-mcp-adapter-0.1.5.rpix": "x"})]
            with mock.patch.object(gs, "SITE", site), mock.patch.object(
                gs, "release_assets", return_value=releases
            ), mock.patch.object(gs, "artifact_sha256", return_value="b" * 64):
                gs.generate_extensions(rpi_repo)
            index = json.loads((site / "api/extensions/index.json").read_text(encoding="utf-8"))
            entry = index["extensions"][0]
            self.assertTrue(entry["deprecated"])
            self.assertEqual(entry["deprecatedMessage"], "MCP is built in.")
            self.assertEqual(entry["deprecatedMessageZh"], "MCP 已内置。")
            detail = json.loads(
                (site / "api/extensions/rpi-mcp-adapter.json").read_text(encoding="utf-8")
            )
            self.assertTrue(detail["deprecated"])
            self.assertEqual(detail["deprecatedMessage"], "MCP is built in.")
            self.assertEqual(detail["deprecatedMessageZh"], "MCP 已内置。")

    def test_generate_extensions_omits_deprecated_for_active_plugins(self):
        deprecation_fields = gs.deprecated_fields({"name": "rpi-todo"})
        self.assertEqual(deprecation_fields, {})


class GithubApiRetryTests(unittest.TestCase):
    """TE47 §8-3：GitHub 抖动重试（连接关闭 / 5xx / 超时）。"""

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    def test_retries_transient_errors_and_succeeds(self):
        calls = {"n": 0}

        def fake_urlopen(_request, timeout=20):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("remote end closed connection")
            return self._Response()

        with mock.patch.object(gs.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(gs.time, "sleep") as sleep:
            self.assertEqual(gs.github_api("https://api.github.com/x"), b'{"ok": true}')
        self.assertEqual(calls["n"], 3, "two failures retried, third attempt served")
        self.assertEqual(sleep.call_count, 2, "backoff between attempts only")

    def test_gives_up_after_attempts_and_raises(self):
        with mock.patch.object(
            gs.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(  # 5xx 同走重试/降级路径
                "https://api.github.com/x", 500, "server error", {}, None
            ),
        ), mock.patch.object(gs.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                gs.github_api("https://api.github.com/x")


def _asset(name: str, size: int | None = None) -> dict:
    """GitHub Release asset entry; the sidecar URL is what artifact_sha256
    fetches when the release carries no `digest`."""
    asset = {"name": name, "browser_download_url": f"https://x/{name}"}
    if size is not None:
        asset["size"] = size
    return asset


def _sha256_of(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


# The index contract (install-packaging.md §3): six targets x two profiles,
# `.zip` for Windows and `.tar.gz` elsewhere, sorted by asset name.
EXPECTED_INDEX_NAMES = [
    "rpi-0.1.8-aarch64-apple-darwin-full.tar.gz",
    "rpi-0.1.8-aarch64-apple-darwin-standard.tar.gz",
    "rpi-0.1.8-aarch64-unknown-linux-gnu-full.tar.gz",
    "rpi-0.1.8-aarch64-unknown-linux-gnu-standard.tar.gz",
    "rpi-0.1.8-aarch64-unknown-linux-musl-full.tar.gz",
    "rpi-0.1.8-aarch64-unknown-linux-musl-standard.tar.gz",
    "rpi-0.1.8-x86_64-pc-windows-msvc-full.zip",
    "rpi-0.1.8-x86_64-pc-windows-msvc-standard.zip",
    "rpi-0.1.8-x86_64-unknown-linux-gnu-full.tar.gz",
    "rpi-0.1.8-x86_64-unknown-linux-gnu-standard.tar.gz",
    "rpi-0.1.8-x86_64-unknown-linux-musl-full.tar.gz",
    "rpi-0.1.8-x86_64-unknown-linux-musl-standard.tar.gz",
]


def _profile_index_names(version: str = "0.1.8") -> list[str]:
    """The 12 standard/full archives the index must pin (fixtures)."""
    return gs.profile_archive_names(version)


def _non_index_names(version: str = "0.1.8") -> list[str]:
    """Assets that must never reach the index: mini base archives, musl
    `-static` fallbacks and `.rpix` plugin artifacts (all with sidecars)."""
    names = []
    for target in gs.PROFILE_TARGETS:
        suffix = "zip" if target == gs.WINDOWS_TARGET else "tar.gz"
        names.append(f"rpi-{version}-{target}.{suffix}")
        names.append(f"rpi-subagents-{version}-{target}.rpix")
    for target in ("x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl"):
        names.append(f"rpi-{version}-{target}-static.tar.gz")
    return names


def _profile_release(version: str = "0.1.8", *, sizes: bool = False) -> dict:
    """Release entry with the 12 indexable archives plus every sidecar and the
    assets excluded from the index (fixture is offline; no digest fields).
    """
    archives = _profile_index_names(version) + _non_index_names(version)
    assets = [
        _asset(name, len(name) * 1024 if sizes else None)
        for name in archives + [f"{name}.sha256" for name in archives]
    ]
    return {"tag_name": f"v{version}", "draft": False, "assets": assets}


class InstallProfileIndexTests(unittest.TestCase):
    """V18-13：`--publish-profiles` 生成扁平 profile 哈希索引。

    fixtures/mock，不联网：`release_assets` 返回构造好的 Release，
    `github_api` 返回 sidecar 内容（`<64-hex>  <basename>`，coreutils 格式）。
    """

    def _publish(
        self,
        site: Path,
        version: str = "0.1.8",
        *,
        release: dict | None = None,
        sidecars: dict[str, str] | None = None,
    ) -> Path:
        release = release if release is not None else _profile_release(version)
        contents = {
            f"{name}.sha256": f"{_sha256_of(name)}  {name}\n"
            for name in _profile_index_names(version)
        }
        contents.update(sidecars or {})

        def fake_github_api(url: str) -> bytes:
            return contents[url.rsplit("/", 1)[-1]].encode("utf-8")

        with mock.patch.object(gs, "SITE", site), mock.patch.object(
            gs, "release_assets", return_value=[release]
        ), mock.patch.object(gs, "github_api", side_effect=fake_github_api):
            return gs.generate_install_profiles(version)

    def _assert_nothing_written(self, site: Path, version: str = "0.1.8") -> None:
        index_dir = site / "api" / "install-profiles"
        self.assertFalse((index_dir / f"{version}.json").exists())
        self.assertEqual(
            [p.name for p in index_dir.glob("*")] if index_dir.exists() else [],
            [],
            "no partial index and no leftover temp file",
        )

    def test_publishes_flat_sorted_index_for_twelve_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            out = self._publish(site, release=_profile_release(sizes=True))
            self.assertEqual(out, site / "api/install-profiles/0.1.8.json")
            text = out.read_text(encoding="utf-8")
            payload = json.loads(text)

        names = EXPECTED_INDEX_NAMES
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), 12)
        self.assertEqual(gs.profile_archive_names("0.1.8"), names)
        self.assertEqual(
            list(payload), ["schemaVersion", "version", "assets", "sizes"]
        )
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["version"], "0.1.8")
        # Flat assets map: asset name -> 64-hex sha256, sorted by asset name.
        self.assertEqual(list(payload["assets"]), names)
        self.assertEqual(list(payload["sizes"]), names)
        for name in names:
            self.assertEqual(payload["assets"][name], _sha256_of(name))
            self.assertRegex(payload["assets"][name], r"^[0-9a-f]{64}$")
            self.assertEqual(payload["sizes"][name], len(name) * 1024)
        # One asset per line, 2-space indent, trailing newline (deterministic).
        self.assertTrue(text.endswith("\n"))
        asset_lines = [line for line in text.splitlines() if line.startswith("    \"")]
        self.assertEqual(
            [line.strip().split('"')[1] for line in asset_lines], names + names
        )

    def test_excludes_mini_static_and_rpix_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            out = self._publish(site)
            payload = json.loads(out.read_text(encoding="utf-8"))
        indexed = set(payload["assets"])
        self.assertEqual(len(indexed), 12)
        for name in _non_index_names():
            self.assertNotIn(name, indexed)
        self.assertFalse(any(name.endswith(".rpix") for name in indexed))
        self.assertFalse(any("-static" in name for name in indexed))
        self.assertNotIn("sizes", payload, "sizes is optional when the API omits it")

    def test_missing_archive_fails_without_writing(self):
        release = _profile_release()
        dropped = "rpi-0.1.8-x86_64-unknown-linux-gnu-full.tar.gz"
        release["assets"] = [a for a in release["assets"] if a["name"] != dropped]
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            with self.assertRaises(SystemExit) as ctx:
                self._publish(site, release=release)
            self.assertIn(dropped, str(ctx.exception))
            self._assert_nothing_written(site)

    def test_missing_sidecar_fails_without_writing(self):
        release = _profile_release()
        dropped = "rpi-0.1.8-aarch64-apple-darwin-standard.tar.gz.sha256"
        release["assets"] = [a for a in release["assets"] if a["name"] != dropped]
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            with self.assertRaises(SystemExit) as ctx:
                self._publish(site, release=release)
            self.assertIn("sha256 sidecars", str(ctx.exception))
            self._assert_nothing_written(site)

    def test_malformed_sidecar_hash_fails_without_writing(self):
        bad = "rpi-0.1.8-x86_64-unknown-linux-musl-standard.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            with self.assertRaises(SystemExit) as ctx:
                self._publish(
                    site,
                    sidecars={f"{bad}.sha256": f"not-a-sha256  {bad}\n"},
                )
            self.assertIn(bad, str(ctx.exception))
            self._assert_nothing_written(site)

    def test_non_hex_anchor_value_is_rejected(self):
        # The index value is the bundle's outer integrity anchor, so it must be
        # 64 hex digits whatever the hash source (release digest or sidecar).
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            with mock.patch.object(gs, "SITE", site), mock.patch.object(
                gs, "release_assets", return_value=[_profile_release()]
            ), mock.patch.object(gs, "artifact_sha256", return_value="sha256:deadbeef"):
                with self.assertRaises(SystemExit) as ctx:
                    gs.generate_install_profiles("0.1.8")
            self.assertIn("invalid sha256", str(ctx.exception))
            self._assert_nothing_written(site)

    def test_version_mismatch_fails_without_writing(self):
        # (a) The requested version has no matching release tag.
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            with self.assertRaises(SystemExit) as ctx:
                self._publish(site, release=_profile_release("0.1.7"))
            self.assertIn(
                "release v0.1.8 not found", str(ctx.exception), "tags must match exactly"
            )
            self._assert_nothing_written(site)
        # (b) The tag matches but the archives carry another version.
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            stale = _profile_release("0.1.7")
            stale["tag_name"] = "v0.1.8"
            with self.assertRaises(SystemExit) as ctx:
                self._publish(site, release=stale)
            self.assertIn("missing profile archives", str(ctx.exception))
            self._assert_nothing_written(site)

    def test_invalid_version_rejected(self):
        for bad in ["0.1", "v0.1.8", "../0.1.8", "0.1.8/", "latest", ""]:
            with tempfile.TemporaryDirectory() as tmp:
                site = Path(tmp)
                with self.assertRaises(SystemExit):
                    self._publish(site, version=bad)
                self.assertFalse((site / "api").exists())

    def test_failure_keeps_a_previous_index_untouched(self):
        release = _profile_release()
        dropped = "rpi-0.1.8-aarch64-unknown-linux-gnu-full.tar.gz"
        release["assets"] = [a for a in release["assets"] if a["name"] != dropped]
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            index = site / "api/install-profiles/0.1.8.json"
            index.parent.mkdir(parents=True)
            index.write_text("{\n  \"schemaVersion\": 1\n}\n", encoding="utf-8")
            before = index.read_bytes()
            with self.assertRaises(SystemExit):
                self._publish(site, release=release)
            self.assertEqual(index.read_bytes(), before)
            self.assertEqual(
                sorted(p.name for p in index.parent.iterdir()), ["0.1.8.json"]
            )

    def test_release_repository_is_the_official_one(self):
        self.assertEqual(gs.RELEASE_REPOSITORY, "revpidev/rpi")


class PublishProfilesCliTests(unittest.TestCase):
    """`--publish-profiles` 的 CLI 行为（独占步骤，先索引后端點）。"""

    def _rpi_repo(self, site: Path) -> Path:
        rpi_repo = site / "rpi"
        (rpi_repo / "crates/rpi-ai/src/providers/data").mkdir(parents=True)
        (rpi_repo / "Cargo.toml").write_text('version = "0.1.8"\n', encoding="utf-8")
        (rpi_repo / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (rpi_repo / "install.ps1").write_text("# ps1\n", encoding="utf-8")
        return rpi_repo

    def _run_main(self, argv: list[str]) -> None:
        """Run main(); the generator's summary lines are captured."""
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            gs.main()

    def test_no_flag_leaves_the_profile_index_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            rpi_repo = self._rpi_repo(site)
            with mock.patch.object(gs, "SITE", site), mock.patch.object(
                gs, "release_assets"
            ) as releases:
                self._run_main(
                    ["generate-site.py", "--rpi-repo", str(rpi_repo), "--version", "0.1.8"]
                )
            releases.assert_not_called()
            self.assertFalse((site / "api/install-profiles").exists())
            self.assertTrue((site / "api/latest-version.json").is_file())

    def test_flag_publishes_only_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            release = _profile_release()
            contents = {
                f"{name}.sha256": f"{_sha256_of(name)}  {name}\n"
                for name in _profile_index_names()
            }

            def fake_github_api(url: str) -> bytes:
                return contents[url.rsplit("/", 1)[-1]].encode("utf-8")

            with mock.patch.object(gs, "SITE", site), mock.patch.object(
                gs, "release_assets", return_value=[release]
            ) as releases, mock.patch.object(gs, "github_api", side_effect=fake_github_api):
                self._run_main(
                    [
                        # --rpi-repo points at a missing tree: the index step
                        # must not need the rpi source checkout (index first,
                        # endpoints after).
                        "generate-site.py",
                        "--rpi-repo",
                        str(site / "absent"),
                        "--publish-profiles",
                        "0.1.8",
                    ]
                )
            releases.assert_called_once_with(gs.RELEASE_REPOSITORY)
            payload = json.loads(
                (site / "api/install-profiles/0.1.8.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["assets"]), 12)
            # The version endpoints and the rest of the site stay untouched.
            self.assertFalse((site / "api/latest-version.json").exists())
            self.assertFalse((site / "api/latest-rc-version.json").exists())
            self.assertFalse((site / "api/extensions").exists())
            self.assertFalse((site / "install.sh").exists())

    def test_flag_refuses_to_combine_with_endpoint_flags(self):
        # Index first, endpoints after: a combined invocation would silently
        # skip one of the two halves of the documented release flow.
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            for extra in (["--version", "0.1.8"], ["--rc-version", "0.1.8-rc.1"]):
                with mock.patch.object(gs, "SITE", site), mock.patch.object(
                    gs, "release_assets"
                ) as releases:
                    with self.assertRaises(SystemExit) as ctx:
                        self._run_main(
                            ["generate-site.py", "--publish-profiles", "0.1.8", *extra]
                        )
                self.assertIn("separate step", str(ctx.exception))
                releases.assert_not_called()
                self.assertFalse((site / "api/install-profiles").exists())


if __name__ == "__main__":
    unittest.main()
