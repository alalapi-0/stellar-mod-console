import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.policy.interfaces import analyze_document, build_interfaces, cns_evidence, lua_evidence, unreal_asset_key
from src.policy.static import archive_metadata


class InterfaceTests(unittest.TestCase):
    def test_keybind_roles_and_whole_modifier_list_are_separate(self):
        evidence = lua_evidence('''IsKeyBindRegistered(Key.F8)
RegisterKeyBind(Key.F8, {ModifierKey.SHIFT, ModifierKey.CONTROL}, function() end)
RegisterKeyBind(0x77, callback)
RegisterKeyBindAsync(Key.NUM_ONE, {ModifierKey.SHIFT, configured}, callback)
custom.RegisterKeyBind(Key.F8, callback)
custom.IsKeyBindRegistered(Key.F8)
register_safe_keybind(Key.F8, callback)
''')['keybind_semantics']
        self.assertEqual([r['role'] for r in evidence], ['QUERY_SPELLING', 'REGISTER_SPELLING',
            'REGISTER_SPELLING', 'REGISTER_SPELLING', 'UNKNOWN_WRAPPER', 'UNKNOWN_WRAPPER', 'UNKNOWN_WRAPPER'])
        self.assertEqual(evidence[1]['candidate_virtual_key'], 119)
        self.assertEqual(evidence[1]['candidate_modifiers'], ['CONTROL', 'SHIFT'])
        self.assertEqual(evidence[2]['candidate_modifiers'], [])
        self.assertEqual(evidence[3]['candidate_virtual_key'], 97)
        self.assertIsNone(evidence[3]['candidate_modifiers'])
        self.assertEqual(evidence[3]['chord_status'], 'UNRESOLVED')

    def test_rebinding_partial_arguments_and_unsupported_keys_stay_unknown(self):
        for text in ['local IsKeyBindRegistered, x = fn, 1; IsKeyBindRegistered(Key.F8)',
                     'RegisterKeyBind = custom; RegisterKeyBind(Key.F8, callback)',
                     'RegisterKeyBind(Key.F8, callback,)', 'RegisterKeyBind(1.5, callback)',
                     'RegisterKeyBind(256, callback)', 'RegisterKeyBind(Key.F25, callback)',
                     'RegisterKeyBind("Key".F8, callback)',
                     'RegisterKeyBind(Key.F8, {"ModifierKey".SHIFT}, callback)',
                     'RegisterKeyBind(Key.F8, {ModifierKey.SHIFT, ModifierKey.SHIFT}, callback)',
                     'RegisterKeyBind(Key.F8, {ModifierKey.SHIFT},',
                     'RegisterKeyBind(Key.F8, {ModifierKey.SHIFT], callback)']:
            with self.subTest(text=text):
                calls = lua_evidence(text)['keybind_semantics']
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]['chord_status'], 'UNRESOLVED')
        valid = lua_evidence('RegisterKeyBind(097, callback)')['keybind_semantics'][0]
        self.assertEqual(valid['candidate_virtual_key'], 97)
        quoted = lua_evidence('RegisterKeyBind(Key.F8, "function")')['keybind_semantics'][0]
        self.assertEqual(quoted['argument_shape'], 'CLOSED')  # A string is not an anonymous callback.

    def test_indirect_calls_are_unknown_and_declarations_are_not_calls(self):
        result = lua_evidence('''function X.RegisterKeyBind(k, f) end
pcall(RegisterKeyBind, Key.F8, callback)
pcall(Env.RegisterKeyBindAsync, Key.F8, callback)
''')
        self.assertEqual(result['keybind_calls'], [])
        self.assertEqual(len(result['keybind_semantics']), 2)
        self.assertTrue(all(r['role'] == 'UNKNOWN_WRAPPER' and r['callee_scope'] == 'INDIRECT'
                            for r in result['keybind_semantics']))

    def test_file_access_modes_preserve_unassigned_targets_and_no_effect_claim(self):
        result = lua_evidence('''-- io.open("ignored", "w")
local prose = "os.remove('ignored')"
io.open("config.json")
io.open(path, "rb")
io.open(path, "w")
io.open(path, "a")
io.open(path, "r+")
io.open(path, mode)
io.open(path, "r++")
io.open(path, "rb+")
os.rename(old, new)
os.remove(path)
function io.open(path, mode) end
''')['file_accesses']
        self.assertEqual([r['intent'] for r in result], ['READ_SPELLING', 'READ_SPELLING',
            'WRITE_SPELLING', 'WRITE_SPELLING', 'WRITE_SPELLING', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'MUTATION_SPELLING', 'MUTATION_SPELLING'])
        self.assertTrue(all(r['write_ownership'] == 'UNASSIGNED' for r in result))
        self.assertTrue(all(r['target_resolution'].startswith('UNVERIFIED') for r in result))
        punctuation = lua_evidence('io.open(")", "w"); io.open(",", "w")')['file_accesses']
        self.assertEqual([r['arguments'][0] for r in punctuation], [[')'], [',']])
        self.assertTrue(all(r['intent'] == 'WRITE_SPELLING' for r in punctuation))

    def test_base_key_reuse_excludes_queries_and_normalizes_numpad_spelling(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog, inventory = {'records': []}, {'sources': [], 'installed': [], 'activation': []}
            for ident, text in [('a', 'RegisterKeyBind(Key.NUM_THREE, {ModifierKey.CONTROL}, callback)'),
                                ('b', 'RegisterKeyBind(99, callback)'), ('query', 'IsKeyBindRegistered(99)')]:
                path = Path(temporary) / (ident + '.zip'); name = ident + '/Scripts/main.lua'; data = text.encode()
                with zipfile.ZipFile(path, 'w') as z: z.writestr(name, data)
                catalog['records'].append({'id': ident, 'kind': 'lua_mod', 'canonical_path': str(path)})
                inventory['sources'].append({'path': str(path), 'entries': [{'path': name, 'size': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'regular': True, 'safe_path': True}]})
            report = build_interfaces(catalog, inventory, {'installed': []})
            group = report['potential_key_reuse'][0]
            self.assertEqual(group['key'], 'VK:99')
            self.assertEqual({m['package'] for m in group['members']}, {'a', 'b'})
            self.assertEqual(report['summary']['raw_lua']['keybind_roles']['QUERY_SPELLING'], 1)

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
