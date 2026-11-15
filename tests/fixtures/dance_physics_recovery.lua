local jobs, hook, post, calls, messages = {}, nil, nil, {}, {}
local travelling, shutting, menu, mode = false, false, false, 0
local function object(id)
    return {IsValid=function() return true end, GetAddress=function() return id end}
end
local player = object(10)
local world, mesh, asset = object(20), object(21), object(22)
player.GetWorld = function() return world end
mesh.GetAnimationMode = function() return mode end
mesh.SkeletalMesh = asset
player.Mesh = mesh
local actor = object(30)
actor.EVE = player
actor.GetClass = function()
    return {GetFullName=function() return "BlueprintGeneratedClass /Game/Mods/Van_Mods_P/ModActor.ModActor_C" end}
end
local target = object(40)
local available = SCENARIO ~= "late-load"
local runtime = {
    state="LOST",
    is_shutting_down=function() return shutting end,
    is_map_travel=function() return travelling end,
    world_is_menu=function() return menu end,
    task_start=function(fn, delay) table.insert(jobs, {fn=fn, delay=delay}) end,
    start_reacquire=function(reason, delay) table.insert(calls, {reason=reason, delay=delay}) end,
}
local api = {
    player=function() return player end,
    find=function() if available then return target end end,
    register=function(path, callback) hook=callback; return 1, 2 end,
    on_map_post=function(callback) post=callback end,
    log=function(message) table.insert(messages, message) end,
}
local function run_one()
    assert(#jobs > 0, "expected scheduled task")
    local job = table.remove(jobs, 1)
    local again = job.fn()
    if type(again) == "number" then table.insert(jobs, {fn=job.fn, delay=again}) end
end
local start = assert(load(MODULE_SOURCE, "@DancePhysicsRecovery.lua"))()
start(runtime, api)
run_one()
if SCENARIO == "late-load" then
    assert(not hook)
    available = true
    run_one()
end
assert(hook and post)
local context = {get=function() return actor end}
if SCENARIO == "foreign-class" then
    actor.GetClass = function() return {GetFullName=function() return "foreign" end} end
elseif SCENARIO == "foreign-pawn" then
    actor.EVE = object(11)
elseif SCENARIO == "active" then runtime.state="ACTIVE"
elseif SCENARIO == "travel" then travelling=true
elseif SCENARIO == "menu" then menu=true
elseif SCENARIO == "shutdown" then shutting=true end
hook(context)
if SCENARIO == "foreign-class" or SCENARIO == "foreign-pawn" or SCENARIO == "active"
    or SCENARIO == "travel" or SCENARIO == "menu" or SCENARIO == "shutdown" then
    assert(#jobs == 0 and #calls == 0, "foreign/unsafe state must not trigger")
    return
end
if SCENARIO == "player-changed" then player = object(12) end
if SCENARIO == "asset-changed" then mesh.SkeletalMesh = object(23) end
if SCENARIO == "world-changed" then world = object(24) end
if SCENARIO == "duplicate" then hook(context) end
if SCENARIO == "active-after-hook" then runtime.state="ACTIVE" end
if SCENARIO == "animation-delayed" or SCENARIO == "animation-timeout" then mode=1 end
run_one()
if SCENARIO == "player-changed" or SCENARIO == "asset-changed" or SCENARIO == "world-changed" or SCENARIO == "active-after-hook" then
    assert(#calls == 0 and #jobs == 0, "stale callback must not trigger")
elseif SCENARIO == "duplicate" then
    assert(#calls == 0 and #jobs == 1, "old event must be superseded")
    run_one()
    assert(#calls == 1 and #jobs == 0)
elseif SCENARIO == "animation-delayed" then
    assert(#calls == 0 and #jobs == 1)
    mode = 0
    run_one()
    assert(#calls == 1 and calls[1].reason == "Van StopAnimation" and calls[1].delay == 150)
elseif SCENARIO == "animation-timeout" then
    while #jobs > 0 do run_one() end
    assert(#calls == 0, "non-native animation must not be overwritten")
else
    assert(#calls == 1 and calls[1].reason == "Van StopAnimation" and calls[1].delay == 150)
    assert(#jobs == 0)
    post()
    assert(#jobs == 0, "map post must not duplicate an installed hook")
end
