"""Ownership and paired native-rebuild policy, using the observed engine model."""
import ctypes
import ctypes.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OwnedLinkedPostProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ctypes.util.find_library("lua5.4")
        if path is None:
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

    def run_case(self, scenario):
        source = (ROOT / "src/runtime/OwnedLinkedPostProcess.lua").read_bytes()
        fixture = (ROOT / "tests/fixtures/owned_linked_postprocess.lua").read_bytes()
        state = self.lua.luaL_newstate()
        self.assertTrue(state)
        try:
            self.lua.luaL_openlibs(state)
            for key, value in [(b"MODULE_SOURCE", source), (b"SCENARIO", scenario.encode())]:
                self.lua.lua_pushlstring(state, value, len(value))
                self.lua.lua_setglobal(state, key)
            code = self.lua.luaL_loadbufferx(state, fixture, len(fixture), b"@owned_linked_postprocess_fixture.lua", b"t")
            if code == 0:
                code = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
            return code, self.lua.lua_tolstring(state, -1, None).decode() if code else None
        finally:
            self.lua.lua_close(state)

    def test_owned_rebuild_and_preservation(self):
        for scenario in ["bounded", "foreign", "foreign-private-tag", "active-montage", "sequence",
                         "custom-pose", "same-current", "private-default", "new-world", "clear-error", "read-error"]:
            with self.subTest(scenario=scenario):
                code, error = self.run_case(scenario)
                self.assertEqual(code, 0, error)

    def test_no_policy_reproduces_registry_growth(self):
        code, error = self.run_case("missing-policy-negative")
        self.assertNotEqual(code, 0)
        self.assertIn("owned linked registry must remain bounded", error)
