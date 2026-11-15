-- Lease the graph reached through our own post-process tag, including nested
-- linked nodes. Also retire the current mesh's exact original PP graph before
-- replacing it. Every registered child must have one of these proven owners.
-- The component's LinkedInstances array is read, never edited by this module.
local M = { VERSION = "1.1.1" }
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
    local metadata = {}
    local function report(text)
        if options.report and reports < 12 then reports = reports + 1; options.report(text) end
    end
    local function context(body)
        if not live(body) then return nil end
        local owner = body:GetOwner()
        if not live(owner) or className(owner) ~= CHARACTER_CLASS
            or not owner:IsPlayerControlled() or not live(owner.Mesh) or id(owner.Mesh) ~= id(body) then return nil end
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
        assert(type(count) == "number" and count >= 0 and count <= 512 and count % 1 == 0, "invalid linked array")
        for index=1,count do
            local child = body.LinkedInstances[index]
            assert(live(child), "invalid registered child")
            result[id(child)] = child:GetFullName()
        end
        return result, count
    end
    local function graphFields(cls)
        local key, name = id(cls), cls:GetFullName()
        if metadata[key] and metadata[key].name == name then return metadata[key] end
        local result = {name=name, fields={}, anim=false}
        local seen, fields, depth = {}, 0, 0
        while live(cls) do
            local address = id(cls)
            depth = depth + 1
            assert(not seen[address] and depth <= 16, "unresolved animation class hierarchy")
            seen[address] = true
            if cls:GetFullName() == "Class /Script/Engine.AnimInstance" then result.anim = true end
            cls:ForEachProperty(function(prop)
                fields = fields + 1
                assert(fields <= 4096, "unresolved animation properties")
                if prop:GetClass():GetFName():ToString() == "StructProperty"
                    and prop:GetStruct():GetFullName() == "ScriptStruct /Script/Engine.AnimNode_LinkedAnimGraph" then
                    result.fields[prop:GetFName():ToString()] = true
                end
            end)
            cls = cls:GetSuperStruct()
        end
        metadata[key] = result
        return result
    end
    local function graph(body, root, includeRoot)
        local result, queue, seen = {}, {root}, {}
        local index = 1
        seen[id(root)] = true
        while index <= #queue do
            local instance = queue[index]
            assert(live(instance) and live(instance:GetOuter()) and id(instance:GetOuter()) == id(body),
                "linked graph belongs to another component")
            local info = graphFields(instance:GetClass())
            assert(info.anim, "linked target is not an animation instance")
            for field in pairs(info.fields) do
                -- TargetInstance is the reflected runtime edge, not InstanceClass
                -- (an authored possibility) or the component-wide registry.
                local target = instance[field].TargetInstance
                if target ~= nil and target:GetAddress() ~= 0 then
                    assert(live(target), "invalid linked graph target")
                    local address = id(target)
                    if not seen[address] then
                        assert(#queue < 512, "linked graph traversal limit")
                        seen[address] = true
                        queue[#queue+1] = target
                        result[address] = target:GetFullName()
                    end
                end
            end
            index = index + 1
        end
        if includeRoot then result[id(root)] = root:GetFullName() end
        return result
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
        -- A foreign replacement under the same tag cannot acquire a lease by
        -- observation. Only our link operation or private baked default can.
        local created = establishedByOwnLink == true or child:GetClass():GetFullName() == DEFAULT_PATH
        if not created and state.owned[address] ~= name then return nil, nil, "unowned_link_preserved" end
        local ok, current = pcall(graph, body, child, true)
        if not ok then
            report("owned graph unresolved: " .. tostring(current))
            return nil, nil, "linked_graph_unresolved"
        end
        if created then
            for childID,childName in pairs(current) do state.owned[childID] = childName end
        end
        return address, current
    end
    function policy:beforeRefresh(body, active, expectedOriginalClass)
        local state = context(body)
        if not state then return false, "not_current_local_eve_body" end
        if not live(active) or not live(body.PostProcessAnimInstance)
            or id(active) ~= id(body.PostProcessAnimInstance) then return false, "postprocess_identity_changed" end
        local _, current, failure = self:observe(body, active)
        if failure then return false, failure end
        current = current or {}
        local outgoing = {}
        -- The caller captured this class from the current mesh before stamping.
        -- Neither a similar class name nor another mesh's PP grants authority.
        if live(expectedOriginalClass) and id(active:GetClass()) == id(expectedOriginalClass)
            and active:GetClass():GetFullName() ~= OWNER_PATH then
            local ok, closure = pcall(graph, body, active, false)
            if not ok then
                report("outgoing graph unresolved: " .. tostring(closure))
                return false, "outgoing_graph_unresolved"
            end
            outgoing = closure
        end
        local registered, count = children(body)
        local stale = false
        for address,name in pairs(registered) do
            if state.owned[address] ~= name and outgoing[address] ~= name then
                return false, "unowned_link_preserved"
            end
            if current[address] ~= name then stale = true end
        end
        -- The exact original PP graph is now fully validated against this
        -- local component's registered children. Preserve that ownership even
        -- when active animation defers the rebuild and the PP is later swapped.
        for address,name in pairs(outgoing) do
            if registered[address] == name then state.owned[address] = name end
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
        local again, againCount = children(body)
        if context(body) ~= state or count ~= againCount
            or not live(body.PostProcessAnimInstance) or id(body.PostProcessAnimInstance) ~= id(active) then
            return false, "graph_context_changed"
        end
        for address,name in pairs(registered) do
            if again[address] ~= name then return false, "graph_context_changed" end
        end
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
    -- A CNS callback can arrive after its mesh changes but before the new PP
    -- instance exists. The caller may recheck once after mode initialization;
    -- all ownership, locality and animation gates are evaluated again.
    function policy:afterInitialize(body, active, expectedOriginalClass, priorReason)
        if priorReason ~= "postprocess_identity_changed" then return false, "not_deferred" end
        return self:beforeRefresh(body, active, expectedOriginalClass)
    end
    return policy
end
return M
