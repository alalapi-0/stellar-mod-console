import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.policy.interfaces import analyze_document, build_interfaces, cns_evidence, lua_evidence, unreal_asset_key
from src.policy.static import archive_metadata


class InterfaceTests(unittest.TestCase):
    def test_comments_and_strings_never_become_writers_or_binds(self):
        evidence = lua_evidence('''-- RegisterKeyBind(Key.F1, function() end)
--[=[ obj.CustomTimeDilation = 3 ]=]
local prose = "SetGlobalTimeDilation() RegisterKeyBind()"
RegisterKeyBind(Key.F8, {ModifierKey.SHIFT}, function() end)
obj.CustomTimeDilation = 0.5
''')
        self.assertEqual(len(evidence["keybind_calls"]), 1)
        self.assertEqual(evidence["keybind_calls"][0]["literal_key"], "KEY:F8")
        self.assertEqual(evidence["keybind_calls"][0]["literal_modifiers"], ["SHIFT"])
        self.assertEqual(evidence["effect_evidence"], [{"domain": "actor_time", "line": 5, "symbol": "CustomTimeDilation", "operation": "DIRECT_ASSIGNMENT"}])

    def test_dynamic_calls_stay_unknown_and_declarations_do_not_count(self):
        evidence = lua_evidence('''function X.RegisterKeyBind(k, f) end
RegisterKeyBind(config.key, callback)
pcall(RegisterKeyBind, keyCode, callback)
''')
        self.assertEqual(len(evidence["keybind_calls"]), 1)
        self.assertIsNone(evidence["keybind_calls"][0]["literal_key"])
        self.assertIn("Indirect", evidence["unknowns"][0]["reason"])
        conditional = lua_evidence('RegisterKeyBind(config.key or Key.F5, callback)')
        self.assertIsNone(conditional["keybind_calls"][0]["literal_key"])
        configured = lua_evidence('return { MovementSpeedKey = "S", MovementSpeedModifiers = { "SHIFT", "ALT" }, }')
        self.assertEqual(len(configured["key_configuration_declarations"]), 2)
        with self.assertRaises(ValueError): lua_evidence('--[[ never closes')

    def test_cns_identity_and_dependencies_preserve_semantic_units(self):
        doc = cns_evidence([{"UniqueFitID": "suit", "Requirement": "CheckDLC", "OutfitDatas": [
            {"Mesh": "/Game/Outfit/Mesh.Mesh", "PonyPhysics": "/Game/Outfit/Physics.Physics"}],
            "Description": "/Game/False/Prose.Prose"}, {"UniqueFitID": "suit", "OutfitPaths": []}])
        self.assertEqual(doc["within_file_duplicate_ids"], {"suit": 2})
        self.assertEqual(doc["records"][0]["declared_data_slots"], 1)
        self.assertEqual(len(doc["records"][0]["references"]), 2)
        self.assertEqual(doc["records"][0]["requirement"], "CheckDLC")
        self.assertEqual(unreal_asset_key("/Game/Outfit/Mesh.Mesh"), "/game/outfit/mesh.uasset")
        self.assertIsNone(unreal_asset_key("file:///unrelated"))
        with self.assertRaises(ValueError): cns_evidence(["invalid"])

    def test_archive_selection_is_bounded_and_never_extracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mod.zip"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Scripts/main.lua", "--metadata")
                z.writestr("asset.ucas", "ignored")
            self.assertEqual(archive_metadata(path, {"Scripts/main.lua"}, 20), {"Scripts/main.lua": b"--metadata"})
            with self.assertRaises(ValueError): archive_metadata(path, {"Scripts/main.lua"}, 3)
            self.assertEqual(list(Path(temporary).iterdir()), [path])
        result = analyze_document(json.dumps([{"UniqueFitID": "x"}]).encode(), "x.dekcns.json")
        self.assertEqual(result["type"], "cns")

    def test_format_recovery_never_accepts_original_and_duplicate_keys_rejected(self):
        original = b'[{"UniqueFitID":"literal,}","OutfitPaths":[],},]'
        result = analyze_document(original, "example.dekcns.json")
        self.assertFalse(result["strict_json_valid"])
        self.assertEqual(result["records"][0]["unique_fit_id"], "literal,}")
        self.assertIn("NOT_ACCEPTED", result["format_status"])
        with self.assertRaises(ValueError):
            analyze_document(b'[{"UniqueFitID":"a","UniqueFitID":"b"}]', "bad.dekcns.json")

    def test_preset_namespace_is_not_a_standalone_entrypoint_or_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog, inventory = {"records": []}, {"sources": [], "installed": [], "activation": []}
            files = {
                "core": {"SB/ue4ss/Mods/Physics/Scripts/main.lua": b'return {}'},
                "preset_a": {"Physics/Scripts/Tweak17.lua": b'return {value=1}'},
                "preset_b": {"Mods/physics/scripts/tweak17.lua": b'return {value=2}'},
                "preset_copy": {"Physics/Scripts/Tweak18.lua": b'return {value=1}'},
                "preset_copy2": {"Physics/Scripts/Tweak18.lua": b'return {value=1}'},
                "helper": {"Physics/Scripts/helpers/main.lua": b'return {}'},
            }
            for ident, entries in files.items():
                path = Path(temporary) / (ident + ".zip")
                with zipfile.ZipFile(path, "w") as z:
                    for name, data in entries.items(): z.writestr(name, data)
                catalog["records"].append({"id": ident, "kind": "lua_mod", "canonical_path": str(path)})
                inventory["sources"].append({"path": str(path), "entries": [
                    {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "regular": True, "safe_path": True} for name, data in entries.items()]})
            result = build_interfaces(catalog, inventory, {"installed": []})
            modules = {m["module"].lower(): m for m in result["module_providers"] if m["module"] == "Physics"}
            self.assertEqual(modules["physics"]["entrypoint_providers"], ["core"])
            self.assertIn("helper", modules["physics"]["extension_providers"])
            self.assertIn("preset_a", modules["physics"]["extension_providers"])
            overlaps = result["raw_script_target_overlaps"]
            self.assertEqual(set(overlaps), {"mods/physics/scripts/tweak17.lua", "mods/physics/scripts/tweak18.lua"})
            self.assertEqual(overlaps["mods/physics/scripts/tweak17.lua"]["status"], "DIFFERENT_SOURCE_BYTES")
            self.assertEqual(overlaps["mods/physics/scripts/tweak18.lua"]["status"], "SAME_SOURCE_BYTES")
            self.assertTrue(all(g["can_apply"] is False for g in overlaps.values()))
            self.assertEqual(len(list(Path(temporary).iterdir())), len(files))


if __name__ == "__main__": unittest.main()
