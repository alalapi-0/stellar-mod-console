"""Exercise the native Body slot policy with UE4SS's 1-based Lua indexing."""

import ctypes
import ctypes.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/runtime/NativeOutfitOwnership.lua"
FIXTURE = ROOT / "tests/fixtures/native_outfit_ownership.lua"


class NativeOutfitOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        library = ctypes.util.find_library("lua5.4")
        if library is None:
            raise unittest.SkipTest("liblua5.4 is required for the synthetic Lua policy test")
        cls.lua = ctypes.CDLL(library)
        cls.lua.luaL_newstate.restype = ctypes.c_void_p
        cls.lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lua.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lua.lua_setglobal.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lua.luaL_loadbufferx.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p
        ]
        cls.lua.lua_pcallk.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_ssize_t, ctypes.c_void_p
        ]
        cls.lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cls.lua.lua_tolstring.restype = ctypes.c_char_p
        cls.lua.lua_close.argtypes = [ctypes.c_void_p]
        cls.source = MODULE.read_bytes()
        cls.fixture = FIXTURE.read_bytes()

    def run_policy(self, source, scenario):
        state = self.lua.luaL_newstate()
        self.assertTrue(state, "Lua state allocation failed")
        try:
            self.lua.luaL_openlibs(state)
            self.lua.lua_pushlstring(state, source, len(source))
            self.lua.lua_setglobal(state, b"MODULE_SOURCE")
            encoded_scenario = scenario.encode()
            self.lua.lua_pushlstring(state, encoded_scenario, len(encoded_scenario))
            self.lua.lua_setglobal(state, b"SCENARIO")
            code = self.lua.luaL_loadbufferx(
                state, self.fixture, len(self.fixture), b"@native_outfit_ownership_fixture.lua", b"t"
            )
            if code == 0:
                code = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
            error = self.lua.lua_tolstring(state, -1, None).decode() if code else None
            return code, error
        finally:
            self.lua.lua_close(state)

    def test_only_explicit_native_changes_remove_override_and_preserve_other_choices(self):
        scenarios = [
            "load-order", "partial-load-order", "cold-initialization", "explicit-native-change",
            "child-set-event", "child-detail-event", "newer-cns-choice", "new-pawn",
            "invalid-saved-data", "unchanged-alias", "non-body-alias", "hidden-native-menu",
            "refresh-event", "show-detail-event",
            "event-after-passive-change", "expired-no-event", "menu-entry-after-passive-change",
            "closed-menu-baseline", "native-action-close-before-poll", "newer-cns-index",
            "newer-cns-same-selection", "cns-other-slot",
            "native-panel-rebuilt",
            "invalid-native-input-lock",
        ]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                code, error = self.run_policy(self.source, scenario)
                self.assertEqual(code, 0, error)

    def test_without_menu_baseline_a_post_action_event_loses_the_handoff(self):
        binding = b"alias=nativeMenuBaseline.alias"
        self.assertEqual(self.source.count(binding), 1)
        without_baseline = self.source.replace(binding, b"alias=lastAlias")
        code, error = self.run_policy(without_baseline, "event-after-passive-change")
        self.assertNotEqual(code, 0, "late native event must detect the lost handoff")
        self.assertIn("explicit native change must remove Body override exactly once", error)

    def test_original_zero_based_index_fails_on_the_realistic_array_layout(self):
        source = self.source
        self.assertEqual(source.count(b"equipment[14]:ToString()"), 1)
        old_index = source.replace(b"equipment[14]:ToString()", b"equipment[13]:ToString()")
        code, error = self.run_policy(old_index, "explicit-native-change")
        self.assertNotEqual(code, 0, "the original off-by-one defect must be detected")
        self.assertIn("explicit native change must remove Body override exactly once", error)

    def test_eager_hook_registration_fails_before_the_menu_blueprint_is_loaded(self):
        timer = b"    LoopInGameThreadWithDelay(500, function()"
        self.assertEqual(self.source.count(timer), 1)
        eager_hook = self.source.replace(
            timer, b"    RegisterHook(nativeHookPaths[1], nativeMenuEvent)\n" + timer
        )
        code, error = self.run_policy(eager_hook, "load-order")
        self.assertNotEqual(code, 0, "the original eager RegisterHook defect must be detected")
        self.assertIn("unloaded menu UFunction must not be hooked", error)


if __name__ == "__main__":
    unittest.main()
