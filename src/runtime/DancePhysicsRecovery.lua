-- Narrow recovery for the Van StopAnimation transition.
-- The caller supplies JiggleControl's existing game-thread task API.
return function(runtime, api)
    local path = "/Game/Mods/Van_Mods_P/ModActor.ModActor_C:StopAnimation"
    local actorClass = "BlueprintGeneratedClass /Game/Mods/Van_Mods_P/ModActor.ModActor_C"
    local installed, installing, stopGeneration = false, false, 0
    local function log(message)
        if api.log then api.log("[DancePhysicsRecovery] " .. message .. "\n") end
    end
    local function live(object)
        local ok, result = pcall(function() return object ~= nil and object:IsValid() end)
        return ok and result == true
    end
    local function address(object)
        local ok, value = pcall(function() return object:GetAddress() end)
        return ok and value or nil
    end
    local function localIdentity(actor)
        if not live(actor) then return nil end
        local ok, pawn, player, class = pcall(function()
            return actor.EVE, api.player(), actor:GetClass():GetFullName()
        end)
        if not ok or class ~= actorClass or not live(pawn) or not live(player) then return nil end
        local a, b = address(pawn), address(player)
        if a == nil or b == nil or a ~= b then return nil end
        local ok2, mesh, asset, world = pcall(function()
            return player.Mesh, player.Mesh.SkeletalMesh, player:GetWorld()
        end)
        if not ok2 or not live(mesh) or not live(asset) or not live(world) then return nil end
        local meshKey, assetKey, worldKey = address(mesh), address(asset), address(world)
        if not meshKey or not assetKey or not worldKey then return nil end
        return {pawn=a, mesh=meshKey, asset=assetKey, world=worldKey}
    end
    local function sameIdentity(a, b)
        return a and b and a.pawn == b.pawn and a.mesh == b.mesh
            and a.asset == b.asset and a.world == b.world
    end
    local function safe()
        return not runtime.is_shutting_down() and not runtime.is_map_travel()
            and runtime.world_is_menu() == false
    end
    local function onStop(context)
        local ok, actor = pcall(function() return context:get() end)
        if not ok or not safe() or runtime.state ~= "LOST" then return end
        local identity = localIdentity(actor)
        if not identity then return end
        stopGeneration = stopGeneration + 1
        local generation = stopGeneration
        local attempts = 0
        runtime.task_start(function()
            if generation ~= stopGeneration or not safe() or runtime.state ~= "LOST"
                or not sameIdentity(localIdentity(actor), identity) then return nil end
            attempts = attempts + 1
            local valid, mode = pcall(function() return api.player().Mesh:GetAnimationMode() end)
            if valid and mode == 0 then
                log("local dance stopped; request bounded physics reacquire")
                runtime.start_reacquire("Van StopAnimation", 150)
                return nil
            end
            if attempts >= 16 then log("animation did not return; no recovery requested"); return nil end
            return 250
        end, 500)
    end
    local function armInstall()
        if installed or installing then return end
        installing = true
        local attempts = 0
        runtime.task_start(function()
            if runtime.is_shutting_down() then installing = false; return nil end
            if runtime.is_map_travel() then installing = false; return nil end
            attempts = attempts + 1
            local ok, target = pcall(api.find, path)
            if ok and live(target) then
                local registered, pre = pcall(api.register, path, onStop)
                if registered and pre ~= nil then
                    installed, installing = true, false
                    log("StopAnimation hook ready")
                    return nil
                end
            end
            if attempts >= 24 then installing = false; log("StopAnimation hook unavailable"); return nil end
            return 250
        end, 0)
    end
    if type(api.on_map_post) ~= "function" or type(api.register) ~= "function"
        or type(api.find) ~= "function" or type(api.player) ~= "function" then
        log("required API unavailable")
        return
    end
    local ok = pcall(api.on_map_post, function()
        stopGeneration = stopGeneration + 1
        armInstall()
    end)
    if not ok then log("map lifecycle unavailable"); return end
    armInstall()
end
