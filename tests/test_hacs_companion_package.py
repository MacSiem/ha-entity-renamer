"""Synthetic packaging tests, no Home Assistant, HACS, GitHub or network."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.stage_hacs_companion import PackagingError, build_companion


class CompanionPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="renamer-m2b-stage-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "repository"
        self.backend = self.source / "custom_components" / "ha_entity_renamer"
        self.backend.mkdir(parents=True)
        self.output = self.root / "companion-stage"
        self.plugin = {
            "name": "Entity Renamer",
            "filename": "ha-entity-renamer.js",
            "content_in_root": True,
            "homeassistant": "2025.6.0",
        }
        self.manifest = {
            "domain": "ha_entity_renamer",
            "name": "Entity Renamer",
            "version": "5.0.0.dev0",
            "config_flow": True,
            "dependencies": ["websocket_api"],
            "codeowners": ["@MacSiem"],
            "documentation": "https://github.com/MacSiem/ha-entity-renamer",
            "issue_tracker": "https://github.com/MacSiem/ha-entity-renamer/issues",
        }
        self.save_metadata()
        for module in ("__init__.py", "const.py", "config_flow.py", "websocket_api.py"):
            (self.backend / module).write_text(f"# {module}\n", encoding="utf-8")
        translations = self.backend / "translations"
        translations.mkdir()
        (translations / "en.json").write_text(json.dumps({"title": "Entity Renamer", "config": {"abort": {"already_configured": "Already configured"}}}) + "\n", encoding="utf-8")
        (self.source / "LICENSE").write_text("MIT example\n", encoding="utf-8")
        (self.source / "ha-entity-renamer.js").write_text("// existing card\n", encoding="utf-8")

    def save_metadata(self):
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "hacs.json").write_text(json.dumps(self.plugin) + "\n")
        (self.backend / "manifest.json").write_text(json.dumps(self.manifest) + "\n")

    def build(self):
        return build_companion(self.source, self.output)

    def test_fresh_stage_has_distinct_integration_layout_and_preserves_plugin(self):
        original = (self.source / "hacs.json").read_bytes()
        source_card = (self.source / "ha-entity-renamer.js").read_bytes()
        info = self.build()
        staged = json.loads((self.output / "hacs.json").read_text())
        self.assertEqual(info["domain"], "ha_entity_renamer")
        self.assertEqual(info["version"], "5.0.0.dev0")
        self.assertEqual(info["homeassistant"], "2025.6.0")
        self.assertEqual(staged["homeassistant"], "2025.6.0")
        self.assertTrue(staged["hide_default_branch"])
        self.assertNotIn("filename", staged)
        self.assertNotIn("content_in_root", staged)
        self.assertEqual(
            [p.name for p in (self.output / "custom_components").iterdir()],
            ["ha_entity_renamer"],
        )
        self.assertFalse((self.output / "ha-entity-renamer.js").exists())
        self.assertEqual((self.source / "hacs.json").read_bytes(), original)
        self.assertEqual((self.source / "ha-entity-renamer.js").read_bytes(), source_card)

    def test_backend_python_json_and_license_are_byte_identical(self):
        (self.backend / "discovery.py").write_bytes(b"# \xc5\xbc\xc3\xb3\xc5\x82w\n")
        self.build()
        for source_file in self.backend.rglob("*"):
            if source_file.is_file():
                stage_file = self.output / source_file.relative_to(self.source)
                self.assertEqual(stage_file.read_bytes(), source_file.read_bytes())
        self.assertEqual((self.output / "LICENSE").read_bytes(), (self.source / "LICENSE").read_bytes())
        readme = (self.output / "README.md").read_text()
        self.assertIn("not a release", readme.lower())
        self.assertIn("Dashboard", readme)

    def test_unknown_runtime_asset_fails_closed(self):
        (self.backend / "native_runtime.dat").write_bytes(b"runtime")
        with self.assertRaisesRegex(PackagingError, "unsupported"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_required_backend_file_fails_before_output(self):
        (self.backend / "websocket_api.py").unlink()
        with self.assertRaisesRegex(PackagingError, "required"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_symlink_source_file_fails_closed(self):
        (self.backend / "discovery.py").symlink_to(self.source / "LICENSE")
        with self.assertRaisesRegex(PackagingError, "symlink"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_existing_target_is_not_overwritten_in_upgrade_scenario(self):
        self.output.mkdir()
        (self.output / "custom-data").write_text("must be retained")
        with self.assertRaisesRegex(PackagingError, "exists"):
            self.build()
        self.assertEqual((self.output / "custom-data").read_text(), "must be retained")

    def test_output_inside_source_is_never_written(self):
        with self.assertRaisesRegex(PackagingError, "overlap"):
            build_companion(self.source, self.source / "new-stage")
        self.assertFalse((self.source / "new-stage").exists())

    def test_rejects_output_named_as_live_homeassistant_component_dir(self):
        with self.assertRaisesRegex(PackagingError, "destination"):
            build_companion(self.source, self.root / "custom_components" / "ha_entity_renamer")
        self.assertFalse((self.root / "custom_components").exists())

    def test_rejects_public_ready_or_missing_version(self):
        self.manifest["version"] = "5.0.0"
        self.save_metadata()
        with self.assertRaisesRegex(PackagingError, "development"):
            self.build()
        self.manifest["version"] = ""
        self.save_metadata()
        with self.assertRaises(PackagingError):
            self.build()

    def test_rejects_wrong_domain_and_invalid_plugin_floor(self):
        self.manifest["domain"] = "other_domain"
        self.save_metadata()
        with self.assertRaisesRegex(PackagingError, "domain"):
            self.build()
        self.manifest["domain"] = "ha_entity_renamer"
        self.plugin["homeassistant"] = "latest"
        self.save_metadata()
        with self.assertRaisesRegex(PackagingError, "Home Assistant"):
            self.build()

    def test_fails_if_plugin_metadata_is_not_the_existing_dashboard_channel(self):
        del self.plugin["filename"]
        self.save_metadata()
        with self.assertRaisesRegex(PackagingError, "Dashboard"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_ignores_compiled_python_cache_but_not_needed_runtime_sources(self):
        cache = self.backend / "__pycache__"
        cache.mkdir()
        (cache / "const.cpython-313.pyc").write_bytes(b"compiled")
        self.build()
        self.assertFalse((self.output / "custom_components/ha_entity_renamer/__pycache__").exists())

    def test_missing_english_translation_fails_closed(self):
        (self.backend / "translations" / "en.json").unlink()
        with self.assertRaisesRegex(PackagingError, "English translation"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_incomplete_english_translation_fails_closed(self):
        (self.backend / "translations" / "en.json").write_text('{"title":"Entity Renamer"}')
        with self.assertRaisesRegex(PackagingError, "English translation"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_output_symlink_to_new_stage_is_refused(self):
        self.output.symlink_to(self.root / "elsewhere-stage")
        with self.assertRaisesRegex(PackagingError, "symlink"):
            self.build()
        self.assertFalse((self.root / "elsewhere-stage").exists())

    def test_output_symlink_ancestor_is_refused(self):
        parent = self.root / "linked"
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(PackagingError, "symlink"):
            build_companion(self.source, parent / "new-stage")

    def test_source_backend_ancestor_symlink_is_refused(self):
        components = self.source / "custom_components"
        moved = self.root / "external-components"
        components.rename(moved)
        components.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(PackagingError, "symlink"):
            self.build()

    def test_plugin_metadata_symlink_is_refused(self):
        metadata = self.source / "hacs.json"
        moved = self.root / "external-hacs.json"
        metadata.rename(moved)
        metadata.symlink_to(moved)
        with self.assertRaisesRegex(PackagingError, "symlink"):
            self.build()

    def test_destination_within_existing_repo_is_refused(self):
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(PackagingError, "existing Git repository"):
            self.build()

    def test_malformed_locale_config_is_refused_cleanly(self):
        (self.backend / "translations/en.json").write_text('{"title":"Entity Renamer","config":[]}')
        with self.assertRaisesRegex(PackagingError, "English translation"):
            self.build()


if __name__ == "__main__":
    unittest.main()
