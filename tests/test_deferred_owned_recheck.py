"""Behavioral tests for the bounded deferred cleanup, using a Lua runtime."""
import ctypes
import ctypes.util
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'src/runtime/DeferredOwnedRecheck.lua'
PATCH = ROOT / 'src/runtime/patch_eve_proportions_deferred.py'
spec = importlib.util.spec_from_file_location('deferred_patch', PATCH)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

LUA = '''
local M = assert(load(MODULE_TEXT))()
local obj = function(address) return {valid=true, GetAddress=function() return address end} end
local character, body, asset=obj(1), obj(2), obj(3)
local queue, game, calls, reports = {}, {}, 0, 0
local currentBody, currentAsset, busy = body, asset, false
local env={live=function(x) return x and x.valid end, body=function() return currentBody end,
 asset=function() return currentAsset end, delay=function(ms, f) assert(ms==3000); queue[#queue+1]=f end,
 onGameThread=function(f) game[#game+1]=f end, busy=function() return busy end,
 recheck=function(b,a) assert(b==body and a==asset); calls=calls+1 end,
 report=function() reports=reports+1 end}
local api=M.new(env)
api:syncTarget(body, asset)
assert(not api:schedule('unowned_link_preserved', character, body, asset) and #queue==0)
assert(api:schedule('active_or_unknown_animation_preserved', character, body, asset))
assert(not api:schedule('active_or_unknown_animation_preserved', character, body, asset) and #queue==1)
if CASE=='duplicate' then api:syncTarget(body, asset) end
if CASE=='different_target_then_return' then api:syncTarget(body, obj(9)); api:syncTarget(body, asset) end
if CASE=='different_body' then currentBody=obj(7) end
if CASE=='different_asset' then currentAsset=obj(8) end
if CASE=='dead' then character.valid=false end
if CASE=='busy' then busy=true end
queue[1]()
assert(calls==0 and #game==1, 'no Unreal access on delay thread')
game[1]()
if CASE=='valid' or CASE=='duplicate' then assert(calls==1) else assert(calls==0) end
if CASE=='busy' then
    assert(#queue==2); queue[2](); game[2]()
    assert(#queue==3); queue[3](); game[3]()
    assert(#queue==3 and calls==0, 'busy retry must stop at three attempts')
else assert(#queue==1) end
assert(reports==0)
'''

RETRY_LUA = '''
local M=assert(load(MODULE_TEXT))()
local obj=function(id) return {GetAddress=function() return id end} end
local character,body,asset=obj(1),obj(2),obj(3)
local timers,game,checks={}, {}, 0
local env={live=function(v) return v~=nil end, body=function() return body end,
 asset=function() return asset end, busy=function() return false end,
 delay=function(ms,fn) assert(ms==3000); timers[#timers+1]=fn end,
 onGameThread=function(fn) game[#game+1]=fn end, report=function(e) error(e) end,
 recheck=function() checks=checks+1; if CASE=='eventual' and checks==3 then return 'owned_native_rebuild' end
 return 'active_or_unknown_animation_preserved' end}
local api=M.new(env); api:syncTarget(body,asset)
assert(api:schedule('active_or_unknown_animation_preserved',character,body,asset))
for i=1,3 do assert(timers[i]); timers[i](); assert(game[i]); game[i]() end
assert(checks==3 and #timers==3, 'retry must be bounded to three')
assert(api:schedule('active_or_unknown_animation_preserved',character,body,asset), 'completed attempt must release pending state')
'''

FAILURE_LUA = '''
local M=assert(load(MODULE_TEXT))()
local obj=function(id) return {GetAddress=function() return id end} end
local character,body,asset=obj(1),obj(2),obj(3)
local queue, reports, failDelay, failQueue = {}, 0, CASE=='delay', CASE=='queue'
local env={live=function(v) return v~=nil end, body=function() return body end,
 asset=function() return asset end, busy=function() return false end,
 delay=function(_, fn) if failDelay then failDelay=false; error('delay failure') end; queue[#queue+1]=fn end,
 onGameThread=function(fn) if failQueue then failQueue=false; error('queue failure') end; fn() end,
 recheck=function() end, report=function() reports=reports+1 end}
local api=M.new(env); api:syncTarget(body,asset)
if CASE=='delay' then
    assert(not pcall(function() api:schedule('active_or_unknown_animation_preserved',character,body,asset) end))
else
    assert(api:schedule('active_or_unknown_animation_preserved',character,body,asset))
    queue[1](); assert(reports==1)
end
assert(api:schedule('active_or_unknown_animation_preserved',character,body,asset), 'failed registration must not leave the target permanently pending')
'''

INTEGRATION = '''
package.preload.DeferredOwnedRecheck = assert(load(MODULE_TEXT))
local timers, game, checks, links = {}, {}, 0, 0
local nextReason = 'active_or_unknown_animation_preserved'
local function ExecuteWithDelay(ms, f) assert(ms==3000); timers[#timers+1]=f end
local function ExecuteInGameThread(f) game[#game+1]=f end
local function live(o) return o ~= nil end
local function getBody(c) return c.body end
local function getMeshAsset(b) return b.asset end
local function stampAsset() return true end
local function rememberOriginal() return 'original' end
local LinkedOwnership = {}
function LinkedOwnership:beforeRefresh(body, active, original)
    assert(original=='original'); checks=checks+1
    if nextReason=='rebuild' then body.pp='rebuilt'; return true,'owned_native_rebuild' end
    return false,nextReason
end
local function linkOriginal(active) assert(active=='rebuilt'); links=links+1 end
local Tuner = nil
local refreshing = false
local refreshQueued = false
local function refreshAnimation(character, source)
    local body = getBody(character)
    local asset = getMeshAsset(body)
    if not live(body) or not live(asset) or not stampAsset(asset, source) then return end
    local active=body:GetPostProcessInstance()
    refreshing = true
    local lifecycleOK, rebuilt, lifecycleReason = pcall(function() return LinkedOwnership:beforeRefresh(body, active, rememberOriginal(asset)) end)
    refreshing = false
    if not lifecycleOK then
        return
    end
end
local asset={GetAddress=function() return 3 end}
local body={asset=asset, pp='old', GetAddress=function() return 2 end}
function body:GetPostProcessInstance() return self.pp end
local character={body=body}
refreshAnimation(character, 'first')
assert(#timers==1 and checks==1)
nextReason='no_stale_owned_child'
refreshAnimation(character, 'duplicate')
assert(#timers==1 and checks==2)
nextReason='rebuild'
timers[1]()
assert(checks==2 and #game==1 and links==0)
game[1]()
assert(checks==3 and links==1 and refreshing==false)
'''

class DeferredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = ctypes.util.find_library('lua5.4')
        if not name:
            raise unittest.SkipTest('liblua5.4 required')
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

    def test_delayed_callback_safety(self):
        for case in ('valid', 'duplicate', 'different_target_then_return', 'different_body', 'different_asset', 'dead', 'busy'):
            with self.subTest(case=case):
                state = self.lua.luaL_newstate(); self.lua.luaL_openlibs(state)
                for key, value in ((b'CASE', case.encode()), (b'MODULE_TEXT', MODULE.read_bytes())):
                    self.lua.lua_pushlstring(state, value, len(value)); self.lua.lua_setglobal(state, key)
                code = self.lua.luaL_loadbufferx(state, LUA.encode(), len(LUA.encode()), b'deferred', b't')
                if not code: code = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
                error = self.lua.lua_tolstring(state, -1, None).decode() if code else None
                self.lua.lua_close(state)
                self.assertEqual(code, 0, error)

    def test_patch_rejects_unknown_or_already_patched(self):
        source = ("local refreshQueued = false\n"
                  "    if not lifecycleOK then\n"
                  "    if not live(body) or not live(asset) or not stampAsset(asset, source) then return end\n")
        result = patcher.patch(source)
        self.assertIn('DeferredOwned:syncTarget(body, asset)', result)
        for item in ('unknown', source+source, result):
            with self.subTest(source=item[:30]), self.assertRaises(ValueError):
                patcher.patch(item)

    def test_generated_integration_rechecks_after_duplicate_notification(self):
        source = patcher.patch(INTEGRATION).encode()
        state = self.lua.luaL_newstate(); self.lua.luaL_openlibs(state)
        module = MODULE.read_bytes()
        self.lua.lua_pushlstring(state, module, len(module)); self.lua.lua_setglobal(state, b'MODULE_TEXT')
        code = self.lua.luaL_loadbufferx(state, source, len(source), b'integration', b't')
        if not code: code = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
        error = self.lua.lua_tolstring(state, -1, None).decode() if code else None
        self.lua.lua_close(state)
        self.assertEqual(code, 0, error)

    def test_registration_failures_allow_a_new_attempt(self):
        for case in ('delay', 'queue'):
            with self.subTest(case=case):
                state = self.lua.luaL_newstate(); self.lua.luaL_openlibs(state)
                for key, value in ((b'CASE', case.encode()), (b'MODULE_TEXT', MODULE.read_bytes())):
                    self.lua.lua_pushlstring(state, value, len(value)); self.lua.lua_setglobal(state, key)
                source=FAILURE_LUA.encode()
                code=self.lua.luaL_loadbufferx(state, source, len(source), b'failure', b't')
                if not code: code=self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
                error=self.lua.lua_tolstring(state, -1, None).decode() if code else None
                self.lua.lua_close(state)
                self.assertEqual(code, 0, error)

    def test_active_animation_rechecks_are_bounded(self):
        for case in ('eventual', 'always_active'):
            with self.subTest(case=case):
                state=self.lua.luaL_newstate(); self.lua.luaL_openlibs(state)
                for key,value in ((b'CASE',case.encode()),(b'MODULE_TEXT',MODULE.read_bytes())):
                    self.lua.lua_pushlstring(state,value,len(value)); self.lua.lua_setglobal(state,key)
                source=RETRY_LUA.encode(); code=self.lua.luaL_loadbufferx(state,source,len(source),b'retry',b't')
                if not code: code=self.lua.lua_pcallk(state,0,0,0,0,None)
                error=self.lua.lua_tolstring(state,-1,None).decode() if code else None
                self.lua.lua_close(state); self.assertEqual(code,0,error)
