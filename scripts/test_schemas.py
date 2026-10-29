#!/usr/bin/env python3
"""Regression tests for the published configuration schemas (V18-04 FR-C).

standard library only; run with `python3 scripts/test_schemas.py`.

The four configuration schemas are generated in the rpi repository by
`scripts/sync-config-schemas.py` from the pinned Pi v1.4.0 models, branded to
`https://revpi.dev/schemas/...` and copied verbatim into this site. These
tests pin the deployed artifact independently of the source checkout:

- every published schema is valid JSON Schema 2020-12 with the revpi.dev $id;
- no pi.dev endpoint survives anywhere in the file;
- the site links each schema and the linked file exists;
- `_headers` serves the schema directory as application/json;
- when the rpi checkout is next to this repository, copies are byte-identical.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
SCHEMAS = (
    "settings.schema.json",
    "models.schema.json",
    "keybindings.schema.json",
    "theme.schema.json",
)
RPI_REPO = ROOT.parent / "rpi"


class PublishedSchemaTests(unittest.TestCase):
    def test_every_schema_is_branded_and_valid(self) -> None:
        for name in SCHEMAS:
            with self.subTest(name=name):
                path = SCHEMA_DIR / name
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("https://pi.dev", text)
                self.assertNotIn('"pi.dev/', text)
                value = json.loads(text)
                self.assertEqual(
                    value["$schema"], "https://json-schema.org/draft/2020-12/schema"
                )
                self.assertEqual(value["$id"], f"https://revpi.dev/schemas/{name}")
                self.assertEqual(
                    value["$comment"],
                    "This file is generated from the pinned Pi v1.4.0 TypeScript "
                    "source (4a080252f) by scripts/sync-config-schemas.py in the rpi "
                    "repository. Do not edit it manually; run the script to refresh it.",
                )
                self.assertIn("revpi.dev", text)

    def test_theme_schema_uses_rpi_app_name(self) -> None:
        value = json.loads((SCHEMA_DIR / "theme.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(value["title"], "Rpi Coding Agent Theme")
        self.assertIn("rpi", value["description"].lower())

    def test_docs_links_resolve_to_published_files(self) -> None:
        docs = (ROOT / "docs.html").read_text(encoding="utf-8")
        for name in SCHEMAS:
            href = f"/schemas/{name}"
            with self.subTest(name=name):
                self.assertIn(href, docs)
                self.assertTrue((ROOT / href.lstrip("/")).is_file())

    def test_headers_serve_schema_json(self) -> None:
        headers = (ROOT / "_headers").read_text(encoding="utf-8")
        block = headers.split("/schemas/*", 1)
        self.assertEqual(len(block), 2, "missing /schemas/* header block")
        self.assertIn("Content-Type: application/json; charset=utf-8", block[1])
        self.assertIn("Cache-Control: public, max-age=3600", block[1])

    def test_site_copies_match_the_rpi_checkout_when_present(self) -> None:
        source = RPI_REPO / "schemas"
        if not source.is_dir():
            self.skipTest("rpi checkout is not adjacent to rpi-pages")
        for name in SCHEMAS:
            with self.subTest(name=name):
                self.assertEqual(
                    (source / name).read_bytes(),
                    (SCHEMA_DIR / name).read_bytes(),
                    f"{name}: site copy is stale; run "
                    "scripts/sync-config-schemas.py in the rpi repository",
                )


if __name__ == "__main__":
    unittest.main()