#!/usr/bin/env python3
"""generate-site.py 生成侧回归（V14-19 RC 更新通道，rpi 自有）。

stdlib unittest，零第三方依赖；`python3 scripts/test_generate_site.py` 直接跑。
覆盖（任务文档 V14-19 FR-G / §4.2）：
- RC 版本号形态判定（R6.1.1：小写 rc、点分数字段、禁前导零）；
- 预发布形态闸：非 rc 形态的预发布 tag 告警并拒入索引（R6.5.3）；
- `latest` 字段排除预发布（rc 不顶掉 stable 展示位）；
- `--rc-version` 写 RC 端点（合法形态校验 + payload schema）；
- semver 排序键的预发布段（rc.10 > rc.9；rc < stable）。
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
