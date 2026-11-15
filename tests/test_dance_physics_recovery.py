"""Exercise the guarded, per-stop recovery policy in Lua without a game."""
import ctypes
import ctypes.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DancePhysicsRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ctypes.util.find_library("lua5.4")
        if not path:
            raise unittest.SkipTest("liblua5.4 required")
        cls.lua = ctypes.CDLL(path)
        cls.lua.luaL_newstate.restype = ctypes.c_void_p
        cls.lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lua.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lua.lua_setglobal.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lua.luaL_loadbufferx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p]
        cls.lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
        cls.lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cls.lua.lua_tolstring.restype = ctypes.c_char_p
        cls.lua.lua_close.argtypes = [ctypes.c_void_p]

    def test_lifecycle_and_identity_guards(self):
        source = (ROOT / "src/runtime/DancePhysicsRecovery.lua").read_bytes()
        fixture = (ROOT / "tests/fixtures/dance_physics_recovery.lua").read_bytes()
        scenarios = ["normal", "late-load", "foreign-class", "foreign-pawn", "active", "travel",
                     "menu", "shutdown", "player-changed", "asset-changed", "world-changed", "duplicate",
                     "active-after-hook", "animation-delayed", "animation-timeout"]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                state = self.lua.luaL_newstate()
                self.assertTrue(state)
                try:
                    self.lua.luaL_openlibs(state)
                    for key, value in [(b"MODULE_SOURCE", source), (b"SCENARIO", scenario.encode())]:
                        self.lua.lua_pushlstring(state, value, len(value))
                        self.lua.lua_setglobal(state, key)
                    result = self.lua.luaL_loadbufferx(state, fixture, len(fixture), b"@dance_physics_recovery_fixture.lua", b"t")
                    if result == 0:
                        result = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
                    error = self.lua.lua_tolstring(state, -1, None).decode() if result else None
                    self.assertEqual(result, 0, error)
                finally:
                    self.lua.lua_close(state)


if __name__ == "__main__":
    unittest.main()
