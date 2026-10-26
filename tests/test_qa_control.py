"""Session bridge policy tests; actual UE4SS bindings need a real game check."""

import ctypes
import ctypes.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QAControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        library = ctypes.util.find_library("lua5.4")
        if library is None:
            raise unittest.SkipTest("liblua5.4 required")
        cls.lua = ctypes.CDLL(library)
        cls.lua.luaL_newstate.restype = ctypes.c_void_p
        cls.lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lua.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lua.lua_setglobal.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lua.luaL_loadbufferx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p]
        cls.lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
        cls.lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cls.lua.lua_tolstring.restype = ctypes.c_char_p
        cls.lua.lua_close.argtypes = [ctypes.c_void_p]
        cls.source = (ROOT / "src/runtime/QAControl.lua").read_bytes()
        cls.fixture = (ROOT / "tests/fixtures/qa_control.lua").read_bytes()
        cls.wardrobe_fixture = (ROOT / "tests/fixtures/qa_wardrobe.lua").read_bytes()

    def test_session_input_policy(self):
        scenarios = ["observe", "move", "stop", "pawn-changed", "clock-reset", "lock-midmove", "nonce", "replay",
                     "locked", "controller-ignored", "pawn-ignored", "native-block", "falling", "expired", "future",
                     "duration", "direction", "identity", "unknown"]
        self.run_scenarios(scenarios, self.fixture)

    def test_session_wardrobe_policy(self):
        self.run_scenarios(["wardrobe", "select", "wrong-mesh", "unrelated-change", "unknown-outfit", "index-oob",
                            "not-registered", "wrong-character", "locked", "moving", "cns-menu", "identity",
                            "native-error", "stop-pending", "legacy-select", "legacy-path-mismatch",
                            "legacy-empty"], self.wardrobe_fixture)

    def run_scenarios(self, scenarios, fixture):
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                state = self.lua.luaL_newstate()
                self.assertTrue(state)
                try:
                    self.lua.luaL_openlibs(state)
                    for name, value in [(b"MODULE_SOURCE", self.source), (b"SCENARIO", scenario.encode())]:
                        self.lua.lua_pushlstring(state, value, len(value))
                        self.lua.lua_setglobal(state, name)
                    code = self.lua.luaL_loadbufferx(state, fixture, len(fixture), b"@qa_control_fixture.lua", b"t")
                    if code == 0:
                        code = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
                    error = self.lua.lua_tolstring(state, -1, None).decode() if code else None
                    self.assertEqual(code, 0, error)
                finally:
                    self.lua.lua_close(state)


if __name__ == "__main__":
    unittest.main()
