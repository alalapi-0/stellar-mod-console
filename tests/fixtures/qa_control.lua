-- Behavioral fixture: models native input consumption and locks, not UE bindings.
local files, calls, tick, cancelled = {}, {}, nil, false
local wall, real, locked, ignored, pawnIgnored, nativeBlock = 1000, 10, 0, false, false, false
local x, vx, pending, pc, owner = 0, 0, false, nil, nil
local cnsOpen = SCENARIO == "cns-menu"
local nextHandle = 73
local function fname(s) return {ToString=function() return s end} end
local world = {IsValid=function() return true end, GetAddress=function() return 100 end}
local move = {MovementMode=1, IsValid=function() return true end, GetAddress=function() return 300 end,
    GetOwner=function() return owner end}
local pawn = {
    IsValid=function() return true end, GetAddress=function() return 200 end,
    GetClass=function() return {GetFName=function() return fname("CH_P_EVE_01_Blueprint_C") end} end,
    GetWorld=function() return world end, GetController=function() return pc end,
    IsPlayerControlled=function() return true end, CharacterMovement=move,
    K2_GetActorLocation=function() return {X=x,Y=0,Z=30} end,
    GetVelocity=function() return {X=vx,Y=0,Z=0} end,
    IsMoveInputIgnored=function() return pawnIgnored end, IsMoveInputBlock=function() return nativeBlock end,
    AddMovementInput=function(self, direction, scale, force)
        assert(not locked and locked ~= 1 or locked == 0)
        assert(not ignored and not pawnIgnored and not nativeBlock, "locked input must not be invoked")
        assert(force == false, "native ignored-input behavior must never be forced")
        assert(scale == 1 and direction.X == 1 and direction.Y == 0 and direction.Z == 0)
        calls[#calls+1] = true
        pending = true
    end,
}
owner = pawn
pc = {IsValid=function() return true end, GetAddress=function() return 400 end,
    GetFName=function() return fname("SBNetworkPlayerController_0") end,
    IsLocalPlayerController=function() return true end, GetWorld=function() return world end,
    IsMoveInputIgnored=function() return ignored end, Pawn=pawn}
setmetatable(pawn, {__index=function(_, key) if key == "MoveInputBlockCount" then return locked end end})
local cns = {IsValid=function() return true end, GetWorld=function() return world end,
    GetClass=function() return {GetFullName=function() return "BlueprintGeneratedClass /Game/Mods/DekCNS_P/ModActor.ModActor_C" end} end,
    TempMenuUI={IsValid=function() return true end, IsInViewport=function() return cnsOpen end}}
FindAllOf=function(name)
    if name == "ModActor_C" then return {cns} end
    assert(name == "SBNetworkPlayerController"); return {pc}
end
StaticFindObject=function(path)
    assert(path == "/Script/Engine.Default__GameplayStatics")
    return {IsValid=function() return true end, GetRealTimeSeconds=function(_, w) assert(w == world); return real end}
end
LoopInGameThreadWithDelay=function(delay, callback) assert(delay == 20); tick=callback; return nextHandle end
CancelDelayedAction=function(handle) assert(handle == nextHandle); cancelled=true end
os.time=function() return wall end
os.remove=function(path) files[path]=nil; return true end
os.rename=function(src, dst)
    assert(files[dst] == nil, "must not depend on replacing an existing file on Windows")
    files[dst], files[src] = assert(files[src]), nil; return true
end
io.open=function(path, mode)
    if mode == "rb" then
        if not files[path] then return nil end
        return {read=function(_, n) return files[path]:sub(1,n) end, close=function() return true end}
    end
    assert(mode == "wb")
    files[path]=""
    return {write=function(_, data) files[path]=files[path]..data; return true end, close=function() return true end}
end
local nonce="fixture_session_nonce_0123456789"
local module=assert(load(MODULE_SOURCE, "@QAControl.lua"))()
local transport=module.start({directory="owned",nonce=nonce})
local function steps(n)
    for _=1,n do
        real=real+0.02; wall=1000+math.floor(real-10)
        vx=pending and 600 or 0; x=x+vx*0.02; pending=false
        if not cancelled then tick() end
    end
end
local function request(id, op, overrides)
    local p={nonce,id,tostring(wall+5),op,"400","1","0","0xc8","0x64"}
    for k,v in pairs(overrides or {}) do p[k]=tostring(v) end
    files["owned/request.txt"]=table.concat(p,"\t").."\n"
    steps(5)
end
local function response(id, phase)
    local text=assert(files["owned/"..id..(phase == "active" and ".active.tsv" or ".response.tsv")], id.." response missing")
    local out={}
    for k,v in text:gmatch("([^\t\n]+)\t([^\n]*)\n") do out[k]=v end
    return out
end
request("observe","observe")
local observed=response("observe")
assert(observed.pawn == "0xc8" and observed.world == "0x64" and tonumber(observed.x) == 0,
    "observe failed: "..tostring(observed.reason))
assert(#calls == 0)
if SCENARIO == "observe" then
    assert(observed.mode == "1" and observed.blocked == "0" and observed.ignored == "false")
elseif SCENARIO == "move" then
    request("move","move"); assert(response("move","active").phase == "active")
    steps(40)
    local out=response("move")
    assert(out.reason == "duration completed" and tonumber(out.horizontal_displacement) > 0)
    assert(tonumber(out.peak_speed) == 600 and out.settled == "true" and out.speed == "0.0")
    assert(#calls > 0 and #calls <= 20)
    local n=#calls; steps(20); assert(#calls == n, "completion must stop input submissions")
elseif SCENARIO == "stop" then
    request("move","move"); steps(2)
    request("stop","stop")
    assert(response("move").reason == "stopped")
    assert(response("stop").phase == "completed")
    local n=#calls; steps(20); assert(#calls == n)
elseif SCENARIO == "pawn-changed" then
    request("move","move"); steps(2)
    pc.Pawn={IsValid=function() return true end, GetAddress=function() return 999 end}
    local n=#calls; steps(5)
    assert(#calls == n and cancelled)
    assert(response("move").reason:find("pawn changed",1,true))
elseif SCENARIO == "clock-reset" then
    request("move","move"); steps(2); real=1; tick()
    assert(cancelled and response("move").reason:find("clock reset",1,true))
elseif SCENARIO == "lock-midmove" then
    request("move","move"); steps(2); locked=1
    local n=#calls; steps(3)
    assert(#calls == n and response("move").reason == "native movement locked or mode changed")
elseif SCENARIO == "cns-midmove" then
    request("move","move");steps(2);cnsOpen=true
    local n=#calls;steps(3)
    assert(#calls == n and response("move").reason == "CNS menu owns movement")
elseif SCENARIO == "cns-menu" then
    request("move","move")
    assert(#calls == 0 and response("move").phase == "rejected" and response("move").reason:find("CNS menu owns movement",1,true))
elseif SCENARIO == "nonce" then
    request("bad","move",{[1]="old_session_nonce_0123456789"})
    assert(not files["owned/bad.response.tsv"] and #calls == 0)
elseif SCENARIO == "replay" then
    request("move","move"); steps(40)
    local n=#calls; request("move","move"); steps(40); assert(#calls == n)
else
    local overrides={}
    if SCENARIO == "locked" then locked=1
    elseif SCENARIO == "controller-ignored" then ignored=true
    elseif SCENARIO == "pawn-ignored" then pawnIgnored=true
    elseif SCENARIO == "native-block" then nativeBlock=true
    elseif SCENARIO == "falling" then move.MovementMode=3
    elseif SCENARIO == "expired" then overrides[3]=wall-1
    elseif SCENARIO == "future" then overrides[3]=wall+30
    elseif SCENARIO == "duration" then overrides[5]=1000
    elseif SCENARIO == "direction" then overrides[6]=2
    elseif SCENARIO == "identity" then overrides[8]="0x999"
    elseif SCENARIO == "unknown" then overrides[4]="execute"
    else error("unknown scenario "..SCENARIO) end
    request("bad","move",overrides)
    assert(response("bad").phase == "rejected" and #calls == 0, "unsafe request must make no native input call")
end
transport.stop()
assert(cancelled, "owned timer must be cancelled")
