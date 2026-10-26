-- Own only children observed under our post-process graph tag. Native class
-- rebuild is permitted only when every registered child belongs to this lease.
-- The component's LinkedInstances array is read, never edited by this module.
local M = { VERSION = "1.0.0" }
local OWNER_CLASS = "ABP_EveProportions_C"
local OWNER_PATH = "AnimBlueprintGeneratedClass /Game/Mods/EveProportions/ABP_EveProportions.ABP_EveProportions_C"
local DEFAULT_PATH = "AnimBlueprintGeneratedClass /Game/Mods/EveProportions/ABP_EvePhysicsPassthrough.ABP_EvePhysicsPassthrough_C"
local MAIN_CLASS = "CH_P_EVE_01_AnimBP_New_C"
local CHARACTER_CLASS = "CH_P_EVE_01_Blueprint_C"
local function live(o) return o ~= nil and o:IsValid() end
local function id(o) return string.format("0x%x", o:GetAddress()) end
local function className(o) return o:GetClass():GetFName():ToString() end

function M.new(options)
    assert(type(options) == "table" and options.tag ~= nil, "owned graph tag required")
    local lease, reports = nil, 0
    local function report(text)
        if options.report and reports < 12 then reports = reports + 1; options.report(text) end
    end
    local function context(body)
        if not live(body) then return nil end
        local owner = body:GetOwner()
        if not live(owner) or className(owner) ~= CHARACTER_CLASS
            or not owner:IsPlayerControlled() or id(owner.Mesh) ~= id(body) then return nil end
        local controller = owner:GetController()
        if not live(controller) or not controller:IsLocalPlayerController()
            or not live(controller.Pawn) or id(controller.Pawn) ~= id(owner) then return nil end
        local world = controller:GetWorld()
        if not live(world) or not live(owner:GetWorld()) or id(owner:GetWorld()) ~= id(world) then return nil end
        local key = id(world) .. " " .. id(owner) .. " " .. id(body) .. " " .. body:GetFullName()
        if lease == nil or lease.key ~= key then lease = {key=key, owned={}} end
        return lease
    end
    local function children(body)
        local result = {}
        local count = body.LinkedInstances:GetArrayNum()
        assert(type(count) == "number" and count >= 0 and count % 1 == 0, "invalid linked array")
        for index=1,count do
            local child = body.LinkedInstances[index]
            assert(live(child), "invalid registered child")
            result[id(child)] = child:GetFullName()
        end
        return result, count
    end
    local policy = {}
    function policy:observe(body, active, establishedByOwnLink)
        local state = context(body)
        if not state or not live(active) or className(active) ~= OWNER_CLASS
            or active:GetClass():GetFullName() ~= OWNER_PATH
            or not live(body.PostProcessAnimInstance) or id(body.PostProcessAnimInstance) ~= id(active) then return end
        local child = active:GetLinkedAnimGraphInstanceByTag(options.tag)
        if not live(child) then return end
        local address, name = id(child), child:GetFullName()
        -- Generic children are claimed only immediately after our own link
        -- operation. The private graph's baked-in passthrough is also ours.
        if establishedByOwnLink == true or child:GetClass():GetFullName() == DEFAULT_PATH then
            state.owned[address] = name
        end
        if state.owned[address] == name then return address end
    end
    function policy:beforeRefresh(body, active)
        local state = context(body)
        if not state then return false, "not_current_local_eve_body" end
        local current = self:observe(body, active)
        local registered, count = children(body)
        local stale = false
        for address,name in pairs(registered) do
            if state.owned[address] ~= name then return false, "unowned_link_preserved" end
            if address ~= current then stale = true end
        end
        for address in pairs(state.owned) do
            if registered[address] == nil then state.owned[address] = nil end
        end
        if not stale then return false, "no_stale_owned_child" end
        local main, originalClass = body.AnimScriptInstance, body.AnimClass
        local safe, eligible = pcall(function()
            if not live(main) or not live(originalClass) or className(main) ~= MAIN_CLASS
                or originalClass:GetFName():ToString() ~= MAIN_CLASS or body:GetAnimationMode() ~= 0
                or main.IsSequenceAnim or main:IsAnyMontagePlaying() then return false end
            for _,name in ipairs({"CustomAnimAlpha", "CustomAnimAlpha2", "CustomAnimAlpha3",
                "CustomAnimAlpha_Upper", "CustomAnimAlpha2_Upper", "CustomAnimAdditiveAlpha",
                "CustomAnimAdditiveAlpha_Upper", "CustomAnimByMeshSlotBodyAlpha"}) do
                local value = main[name]
                if type(value) ~= "number" or value ~= value or math.abs(value) > 0.0001 then return false end
            end
            return true
        end)
        if not safe or not eligible then return false, "active_or_unknown_animation_preserved" end
        local mesh = body.SkeletalMesh
        assert(live(mesh), "body mesh unavailable")
        local meshID, ownerID, classID = id(mesh), id(body:GetOwner()), id(originalClass)
        -- SB's native SetAnimClass(nil) uninitializes instances and clears its
        -- registry. Merely unlinking a graph tag leaves registry entries behind.
        local cleared, clearError = pcall(function() body:SetAnimClass(nil) end)
        local countRead, clearCount = pcall(function() return body.LinkedInstances:GetArrayNum() end)
        -- Restoration is the paired operation, even if the clear call failed.
        local restored, restoreError = pcall(function() body:SetAnimClass(originalClass) end)
        assert(restored, "native class restoration failed: " .. tostring(restoreError))
        assert(cleared and countRead and clearCount == 0,
            "native owned registry clear failed: " .. tostring(clearError))
        assert(live(body.SkeletalMesh) and id(body.SkeletalMesh) == meshID
            and id(body:GetOwner()) == ownerID and live(body.AnimScriptInstance)
            and id(body.AnimScriptInstance:GetClass()) == classID, "native rebuild changed protected identity")
        state.owned = {}
        self:observe(body, body.PostProcessAnimInstance)
        report("native owned rebuild " .. count .. " old links; class/mesh/owner restored")
        return true, "owned_native_rebuild"
    end
    return policy
end
return M
