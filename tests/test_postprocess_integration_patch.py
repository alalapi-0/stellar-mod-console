"""Exercise the generated Lua integration with native-like callback outcomes."""
import ctypes
import ctypes.util
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "post_init_patch", ROOT / "src/runtime/patch_eve_proportions_post_init.py"
)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

SOURCE = '''
local function run(body, active, asset, source, initialized, LinkedOwnership, live, rememberOriginal, linkOriginal)
    local refreshing = false
    local lifecycleOK, rebuilt = pcall(function() return LinkedOwnership:beforeRefresh(body, active, rememberOriginal(asset)) end)
    local after = nil
    pcall(function() after = body:GetPostProcessInstance() end)
    linkOriginal(after, asset, source, body)
end
'''

HARNESS = '''
local reason, initialized, throws = CASE, true, false
if CASE == "failure" then reason="postprocess_identity_changed";throws=true end
if CASE == "not_initialized" then reason="postprocess_identity_changed";initialized=false end
local body={current="new-pp"}
function body:GetPostProcessInstance() return self.current end
local policy={calls=0}
function policy:beforeRefresh() return false, reason end
function policy:afterInitialize(b, pp, original, prior)
    self.calls=self.calls+1
    assert(pp=="new-pp" and original=="original-class" and prior==reason)
    if throws then error("injected native failure") end
    b.current="rebuilt-pp"
    return true
end
local linked=nil
local function link(pp) linked=pp end
run(body,"old-pp","asset","source",initialized,policy,function(x) return x~=nil end,
    function() return "original-class" end,link)
if CASE == "postprocess_identity_changed" then
    assert(policy.calls==1 and linked=="rebuilt-pp", "must relink the rebuilt instance")
elseif CASE == "failure" then
    assert(policy.calls==1 and linked==nil, "failed recheck must stop linking")
else
    assert(policy.calls==0 and linked=="new-pp", "other paths must not recheck")
end
'''


class IntegrationPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = ctypes.util.find_library("lua5.4")
        if not name:
            raise unittest.SkipTest("liblua5.4 required")
        cls.lua = ctypes.CDLL(name)
        cls.lua.luaL_newstate.restype = ctypes.c_void_p
        cls.lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lua.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lua.lua_setglobal.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lua.luaL_loadbufferx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p]
        cls.lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
        cls.lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cls.lua.lua_tolstring.restype = ctypes.c_char_p
        cls.lua.lua_close.argtypes = [ctypes.c_void_p]

    def test_callback_flow(self):
        source = (patcher.patch(SOURCE) + HARNESS).encode()
        for case in ["postprocess_identity_changed", "unowned_link_preserved", "failure", "not_initialized"]:
            with self.subTest(case=case):
                state = self.lua.luaL_newstate()
                self.lua.luaL_openlibs(state)
                value = case.encode()
                self.lua.lua_pushlstring(state, value, len(value))
                self.lua.lua_setglobal(state, b"CASE")
                code = self.lua.luaL_loadbufferx(state, source, len(source), b"integration", b"t")
                if not code:
                    code = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
                error = self.lua.lua_tolstring(state, -1, None).decode() if code else None
                self.lua.lua_close(state)
                self.assertEqual(code, 0, error)

    def test_rejects_unknown_or_already_patched_source(self):
        for source in ["unknown", SOURCE + SOURCE, patcher.patch(SOURCE)]:
            with self.subTest(length=len(source)), self.assertRaises(ValueError):
                patcher.patch(source)
