from pathlib import Path
import shutil
import subprocess
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
import zipfile

import test_profile_compare as fixtures
from wabbajack_viewer import WabbajackGuideApp


class ViewerParsingTests(unittest.TestCase):
    def setUp(self):
        self.app = WabbajackGuideApp.__new__(WabbajackGuideApp)
        self.app.current_wabbajack_file = "/tmp/example.wabbajack"

    def test_modlist_comments_bom_and_foreign_entries(self):
        entries = self.app.parse_modlist_entries("\ufeff# Automatically generated\n+Alpha\n-Beta\n*DLC: Dawnguard\n")
        self.assertEqual([e["raw_name"] for e in entries], ["DLC: Dawnguard", "Beta", "Alpha"])

    def test_mod_order_export_preserves_authored_order_while_browsing_is_sorted(self):
        self.app.mod_rows = [{'number': 1, 'raw_name': 'Zeta', 'name': 'Zeta', 'active': True},
                             {'number': 2, 'raw_name': 'Alpha', 'name': 'Alpha', 'active': False}]
        self.app.sort_column, self.app.sort_reverse = 'name', False
        self.assertEqual(self.app.build_modlist_txt(), '-Alpha\r\n+Zeta\r\n')

    def test_tolerant_metadata_reader(self):
        meta = self.app.parse_meta_ini('\ufeff[General]\nVERSION="1.0"\nversion="2.0"\ninstallationFile="archive.zip"\nnotes=100%\n')
        self.assertEqual(meta["version"], "2.0")
        self.assertEqual(meta["installation_file"], "archive.zip")

    def test_short_and_legacy_downloader_names_and_cdn_links(self):
        for kind in ("Nexus", "NexusDownloader, Wabbajack.Lib"):
            links = self.app.build_archive_links({"State": {"$type": kind, "GameName": "SkyrimSE", "ModID": 123, "FileID": 456}})
            self.assertEqual(links["page"], "https://www.nexusmods.com/skyrimspecialedition/mods/123")
            self.assertIn("file_id=456", links["download"])
        links = self.app.build_archive_links({"State": {"$type": "WabbajackCDN", "Url": "https://example.org/file"}})
        self.assertEqual(links["download"], "https://example.org/file")
        self.assertEqual(self.app.build_archive_links({"State": {"$type": "Http", "Url": "javascript:bad"}})["download"], "")

    def test_legacy_directive_aliases_textures_and_full_nested_paths(self):
        self.app.directives = [
            {"$type": "InlineFile, Wabbajack.Lib", "To": "mods/Test/embedded.txt", "SourceDataID": "one"},
            {"$type": "RemappedInlineFile, Wabbajack.Lib", "To": "mods/Test/config.ini", "SourceDataID": "two"},
            {"$type": "TransformedTexture, Wabbajack.Lib", "To": "mods/Test/texture.dds", "ArchiveHashPath": ["hash", "inner.bsa", "inside.dds"]},
        ]
        self.app.mod_folder_files = {}
        self.app.mod_folder_archive_hashes = {}
        self.app.synthetic_archive_by_hash = {}
        self.app.process_directives()
        files = self.app.mod_folder_files["test"]
        self.assertEqual(len(files), 3)
        self.assertEqual(files[2]["archive_path"], "inner.bsa → inside.dds")
        self.assertEqual(files[2]["archive_hash_path"], ["hash", "inner.bsa", "inside.dds"])
        class Archive:
            def namelist(self):
                return ["one", "two"]
        self.assertEqual(len(self.app.load_inline_file_sources(Archive())), 2)

    def test_profile_discovery_does_not_select_tool_profile_or_nested_modlist(self):
        self.app.inline_file_sources = {"tools\\other\\modlist.txt": "one", "mods\\tool\\modlist.txt": "two",
                                        "profiles\\right\\modlist.txt": "three"}
        with patch.object(self.app, "read_inline_text", return_value="+Test\n"):
            self.app.load_profile_inline_data()
        self.assertEqual(self.app.active_profile, "profiles\\right")
        self.assertEqual(self.app.all_profile_paths, ["profiles\\right\\modlist.txt"])

    def test_file_tree_preserves_instruction_and_recorded_hash(self):
        result = self.app.organize_files_by_directory([{"target_path": "mods/Test/output.bsa", "archive_path": "", "size": 0,
                                                      "directive_type": "CreateBSA", "hash": "recorded-hash"}], "Test")
        self.assertEqual(result["output.bsa"]["kind"], "CreateBSA")
        self.assertEqual(result["output.bsa"]["hash"], "recorded-hash")

    def row(self, directive_type="FromArchive", depth=2):
        return {"raw_name": "Mod $(touch should-not-exist)", "source_archives": [{"Hash": "archive", "Name": "source.zip"}],
                "folder_files": [{"target_path": "mods\\Mod $(touch should-not-exist)\\meshes\\thing.nif",
                                  "archive_path": "data\\meshes\\thing.nif", "archive_hash": "archive",
                                  "directive_type": directive_type, "archive_depth": depth}]}

    def test_patch_remap_generated_and_nested_archive_exports_are_blocked(self):
        for kind, depth in (("PatchedFromArchive", 2), ("RemappedInlineFile", 0), ("CreateBSA", 0), ("FromArchive", 3)):
            for method in (self.app.build_bash_install_script, self.app.build_batch_install_script):
                with self.subTest(kind=kind, method=method.__name__), self.assertRaises(ValueError):
                    method(self.row(kind, depth))

    def test_unsafe_destinations_and_embedded_ids_are_rejected(self):
        row = self.row()
        row["folder_files"][0]["target_path"] = "mods\\Mod $(touch should-not-exist)\\..\\escape"
        with self.assertRaises(ValueError):
            self.app.build_bash_install_script(row)
        row = self.row("InlineFile", 0)
        row["folder_files"][0].update(archive_hash="INLINEFILE::mod", source_data_id="$(touch unexpected)")
        with self.assertRaises(ValueError):
            self.app.build_bash_install_script(row)

    def test_batch_jumps_over_subroutine(self):
        text = self.app.build_batch_install_script(self.row())
        self.assertLess(text.index("goto :install_start"), text.index(":resolve_archive\r\n"))
        self.assertLess(text.index("exit /b\r\n", text.index("\r\n:resolve_archive\r\n")), text.index("\r\n:install_start\r\n"))
        self.assertNotIn("\r\r\n", text)

    @unittest.skipUnless(shutil.which("7z") and shutil.which("bash"), "7z and bash needed")
    def test_bash_installer_uses_linux_paths_and_literal_folder_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "source.zip", "w") as archive:
                archive.writestr("data/meshes/thing.nif", b"model content")
            script = root / "install.sh"
            script.write_text(self.app.build_bash_install_script(self.row()))
            completed = subprocess.run(["bash", str(script)], cwd=root, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((root / self.row()["raw_name"] / "meshes/thing.nif").read_bytes(), b"model content")
            self.assertFalse((root / "should-not-exist").exists())


class ViewerInterfaceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.fixture = fixtures.ComparisonTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)

    def app(self):
        with patch.object(WabbajackGuideApp, "load_persisted_preferences"), patch.object(WabbajackGuideApp, "auto_load_last_saved_modlist"):
            app = WabbajackGuideApp(self.root)
        app.config_path = self.fixture.root / 'viewer-preferences.json'
        return app

    def test_snapshot_restores_profile_switch_and_failed_load_retains_previous(self):
        app = self.app()
        package = self.fixture.package(profiles=("One", "Two"))
        with patch("wabbajack_viewer.messagebox.showinfo"):
            app.extract_and_load_modlist(str(package))
        self.assertEqual(len(app.mod_rows), 2)
        self.assertEqual(str(app.profile_combobox["state"]), "readonly")
        snapshot = self.fixture.root / "snapshot.wvmodlist.json.gz"
        with patch("wabbajack_viewer.filedialog.asksaveasfilename", return_value=str(snapshot)), \
             patch("wabbajack_viewer.messagebox.showinfo"), patch.object(app, "remember_saved_modlist_path"):
            app.save_modlist(wait=True)
            self.assertTrue(app.load_saved_modlist_from_path(snapshot))
        self.assertEqual(str(app.profile_combobox["state"]), "readonly")
        rows = list(app.mod_rows)
        with patch("wabbajack_viewer.messagebox.showinfo"):
            app.on_profile_selected()
        self.assertEqual(app.mod_rows, rows)
        with patch("wabbajack_viewer.messagebox.showerror"):
            app.extract_and_load_modlist(str(self.fixture.root / "missing.wabbajack"))
        self.assertEqual(app.mod_rows, rows)
        self.assertEqual(app.current_wabbajack_file, str(package))


if __name__ == "__main__":
    unittest.main()
