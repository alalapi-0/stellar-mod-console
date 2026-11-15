import hashlib
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from src.catalog.inventory import inspect_file, run, safe_entry


class InventoryTests(unittest.TestCase):
    def test_traversal_is_detected_without_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mod.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("../protected.txt", "do not deploy")
                z.writestr("good/main.lua", "return true")
            row = inspect_file(p)
            self.assertEqual(row["integrity"], "UNSAFE_ENTRIES")
            self.assertEqual(len(row["entries"]), 2)
            self.assertEqual(list(Path(d).iterdir()), [p])
            self.assertEqual(row["entries"][1]["sha256"], hashlib.sha256(b"return true").hexdigest())

    def test_crc_failure_and_incomplete_are_not_usable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.zip"
            with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_STORED) as z:
                z.writestr("payload", "unique payload value")
            p.write_bytes(p.read_bytes().replace(b"unique payload value", b"broken payload value"))
            self.assertEqual(inspect_file(p)["integrity"], "DECODE_FAILED")
            partial = Path(d) / "candidate.zip.crdownload"
            partial.write_bytes(b"partial")
            self.assertEqual(inspect_file(partial)["integrity"], "INCOMPLETE")

    def test_concurrent_change_invalidates_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "file.bin"
            p.write_bytes(b"before")
            def mutate(path):
                path.write_bytes(b"after")
                return "synthetic_hash"
            with patch("src.catalog.inventory.sha256", side_effect=mutate):
                self.assertEqual(inspect_file(p)["integrity"], "SOURCE_CHANGED")

    def test_duplicate_entries_and_windows_paths(self):
        self.assertFalse(safe_entry("C:\\game\\asset"))
        self.assertFalse(safe_entry("\\\\server\\share"))
        self.assertFalse(safe_entry("mods\\..\\save"))
        self.assertTrue(safe_entry("中文/main.lua"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "duplicate.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("mod/main.lua", "one")
                z.writestr("mod\\main.lua", "two")
            self.assertEqual(inspect_file(p)["integrity"], "UNSAFE_ENTRIES")

    def test_resume_rehashes_and_rejects_changed_crc(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            downloads = root / "Downloads"
            downloads.mkdir()
            workspace = root / "previous"
            (workspace / "dependencies").mkdir(parents=True)
            (workspace / "remaining-packages.json").write_text('{"packages":[],"other_games":[]}')
            (workspace / "tool-packages.json").write_text('[]')
            (workspace / "state.json").write_text('{"archives":[],"installed_files":[],"original_game_files":[]}')
            game = root / "steamapps/common/Game"
            mods = game / "SB/Binaries/Win64/ue4ss/Mods"
            mods.mkdir(parents=True)
            (mods / "mods.txt").write_text("Example : 0\n")
            (mods.parents[1] / "dwmapi.dll").write_bytes(b"synthetic loader")
            (game.parents[1] / "appmanifest_3489700.acf").write_text('"buildid" "123"')
            config = root / "paths.json"
            config.write_text(json.dumps({"paths": {"downloads": str(downloads), "game": str(game),
                                                     "existing_mod_workspace": str(workspace)}}))
            archive = downloads / "mod.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as z:
                z.writestr("main.lua", "unique payload value")
            output = root / "output"
            with contextlib.redirect_stdout(io.StringIO()):
                run(config, output)
                original = json.loads((output / "inventory.json").read_text())["sources"][0]
                archive.write_bytes(archive.read_bytes().replace(b"unique payload value", b"broken payload value"))
                run(config, output, resume=True)
            changed = json.loads((output / "inventory.json").read_text())["sources"][0]
            self.assertEqual(original["integrity"], "DECODED")
            self.assertEqual(changed["integrity"], "DECODE_FAILED")
            self.assertNotEqual(original["sha256"], changed["sha256"])


if __name__ == "__main__":
    unittest.main()
