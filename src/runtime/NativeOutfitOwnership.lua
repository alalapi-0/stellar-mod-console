-- Native equipment identity and CNS mesh overrides are distinct owners.
-- Native Body slot=13; UE4SS Lua TArray is 1-indexed, so read Lua index14.
local M = {}
function M.start(getCNS, log)
    local lastPawn, lastAlias = nil, nil
    local lastError = nil
    local nativeRequest = nil
    local nativeMenuBaseline = nil
    local cnsSelectionHookReady = false
    local lastWait, waitMessages = nil, 0
    local function waiting(reason)
        if reason ~= lastWait and waitMessages < 24 then
            log("Native wardrobe waiting | " .. reason)
            lastWait, waitMessages = reason, waitMessages + 1
        end
    end
    local function live(object)
        return object ~= nil and object:IsValid()
    end
    local function currentPawn(cns)
        local world = cns:GetWorld()
        if not live(world) then return nil, "CNS world unavailable" end
        local controllers = FindAllOf("SBNetworkPlayerController") or {}
        for _, pc in ipairs(controllers) do
            if live(pc) and pc:IsLocalPlayerController() and live(pc.Pawn)
                and pc.Pawn:GetClass():GetFName():ToString() == "CH_P_EVE_01_Blueprint_C"
                and pc:GetWorld():GetAddress() == world:GetAddress() then
                return pc.Pawn
            end
        end
        return nil, "No matching local EVE controller in CNS world; candidates=" .. #controllers
    end
    local function bodyAlias(pawn)
        local equipment = pawn.EquipmentDataList
        if equipment:GetArrayNum() ~= 14 then return nil end
        local alias = equipment[14]:ToString()
        return type(alias) == "string" and alias:sub(1, 3) == "BS_" and alias or nil
    end
    local function text(value)
        return type(value) == "string" and value or value:ToString()
    end
    local function bodySelection(cns)
        local saved = cns.SavedModData
        if not live(saved) or saved:GetClass():GetFName():ToString() ~= "BP_CNS_SaveData_C" then return nil end
        local present = saved.LastActiveOutfitMap:Contains("EVE-Body-")
        local id = present and text(saved.LastActiveOutfitMap:Find("EVE-Body-"):get()) or ""
        local index = id ~= "" and saved.LastOutfitIndexMap:Contains(id)
            and tonumber(saved.LastOutfitIndexMap:Find(id):get()) or -1
        return {present=present, id=id, index=index}
    end
    local function sameSelection(a, b)
        return a and b and a.present == b.present and a.id == b.id and a.index == b.index
    end
    -- Native initialization also changes equipment IDs. Only a real equipment-menu
    -- event may arm a handoff, and it remains tied to the observed Pawn/baseline.
    local nativeHookPaths = {}
    for _, name in ipairs({"WB_Equip_MainTab", "WB_Equip_Set", "WB_Equip_SelectDetail"}) do
        nativeHookPaths[#nativeHookPaths + 1] = "/Game/Art/UI/Widget/GameMenu/Equip/" .. name .. "." .. name .. "_C:EventNotification"
    end
    local nativeHookReady = {}
    local function nativeMenuEvent(context, event)
        local ok, reason = pcall(function()
            local widget, cns = context:get(), getCNS()
            if not live(widget) or not live(cns) or not widget:IsVisible() then return end
            local value = event:get()
            local eventName = type(value) == "string" and value or value:ToString()
            if eventName ~= "EquipedDetail" then return end
            local pawn = currentPawn(cns)
            if not pawn or pawn:GetAddress() ~= lastPawn or not lastAlias then return end
            local menu = cns.TempMenuUI
            if live(menu) and menu:IsInViewport() then
                nativeRequest, nativeMenuBaseline = nil, nil
                return
            end
            -- Polling can see the new equipment before EquipedDetail arrives.
            -- Keep the menu's original identity across that passive observation;
            -- an expired request never authorizes a handoff by itself.
            local selection = bodySelection(cns)
            if not nativeMenuBaseline or nativeMenuBaseline.pawn ~= lastPawn
                or not sameSelection(nativeMenuBaseline.selection, selection) then
                local alias = bodyAlias(pawn)
                if not alias then return end
                nativeMenuBaseline = {pawn=lastPawn, alias=alias, selection=selection}
            end
            nativeRequest = {pawn=lastPawn, alias=nativeMenuBaseline.alias, polls=12}
            log("Native wardrobe menu event | " .. eventName)
        end)
        if not ok then log("Native wardrobe menu event refused | " .. tostring(reason)) end
    end
    local function ensureNativeHook()
        for _, path in ipairs(nativeHookPaths) do
            if not nativeHookReady[path] and live(StaticFindObject(path)) then
                RegisterHook(path, nativeMenuEvent)
                nativeHookReady[path] = true
                log("Native wardrobe menu observer ready | " .. path)
            end
        end
        local path = "/Game/Mods/DekCNS_P/ModActor.ModActor_C:Set Custom Outfit"
        if not cnsSelectionHookReady and live(StaticFindObject(path)) then
            RegisterHook(path, function(context, character, fitType, subType)
                local ok, reason = pcall(function()
                    local cns = getCNS()
                    local actor = context:get()
                    if not live(cns) or not live(actor) or actor:GetAddress() ~= cns:GetAddress() then return end
                    if text(character:get()) == "EVE" and text(fitType:get()) == "Body"
                        and text(subType:get()) == "" then
                        nativeRequest, nativeMenuBaseline = nil, nil
                        log("Native wardrobe pending request cancelled by explicit CNS Body selection")
                    end
                end)
                if not ok then log("Native wardrobe CNS selection observer refused | " .. tostring(reason)) end
            end)
            cnsSelectionHookReady = true
        end
    end
    LoopInGameThreadWithDelay(500, function()
        if nativeRequest then
            nativeRequest.polls = nativeRequest.polls - 1
            if nativeRequest.polls <= 0 then nativeRequest = nil end
        end
        local ok, reason = pcall(function()
            ensureNativeHook() -- native menu Blueprint is loaded only on demand
            local cns = getCNS()
            if not live(cns) then waiting("CNS unavailable"); return end
            local class = cns:GetClass():GetFullName()
            if not class:find("/Game/Mods/DekCNS_P/ModActor", 1, true) then waiting("Unexpected CNS class: " .. class); return end
            local pawn, waitReason = currentPawn(cns)
            if not pawn then waiting(waitReason); return end
            local alias = bodyAlias(pawn)
            if not alias then waiting("Body equipment identity unavailable"); return end
            lastWait = nil
            local address = pawn:GetAddress()
            if address ~= lastPawn then
                lastPawn, lastAlias = address, alias
                nativeRequest, nativeMenuBaseline = nil, nil
                log("Native wardrobe baseline | " .. alias)
                return
            end
            -- A newer explicit CNS menu action wins over an earlier native request.
            local menu = cns.TempMenuUI
            if live(menu) and menu:IsInViewport() then
                nativeRequest, nativeMenuBaseline, lastAlias = nil, nil, alias
                return
            end
            local selection = bodySelection(cns)
            local lockCount = tonumber(pawn.MoveInputBlockCount)
            if not lockCount or lockCount < 0 then waiting("Native input lock state unavailable"); return end
            if nativeMenuBaseline then
                if selection and not sameSelection(nativeMenuBaseline.selection, selection) then
                    nativeRequest, nativeMenuBaseline = nil, nil
                elseif lockCount == 0 then
                    -- Individual equipment panels hide/rebuild while the native
                    -- menu still owns input. An unlocked Pawn ends that menu
                    -- baseline, not its already-authorized asynchronous request.
                    nativeMenuBaseline = nil
                end
            end
            if not nativeRequest or nativeRequest.pawn ~= address then
                lastAlias = alias -- passive initialization/recovery is never a wardrobe request
                return
            end
            if alias == nativeRequest.alias then lastAlias = alias; return end
            -- Actor deselection removes the live mesh. Change only the author's
            -- saved selection and serialize current data; native equipment owns it.
            local saved = cns.SavedModData
            if not live(saved) or saved:GetClass():GetFName():ToString() ~= "BP_CNS_SaveData_C" then
                waiting("CNS saved selection unavailable"); return
            end
            -- An empty saved Body ID means explicit removal in CNS. Absence of
            -- the single override key lets the equipped native mesh survive.
            saved.LastActiveOutfitMap:Remove("EVE-Body-")
            saved.RemovedOutfitMap:Remove("EVE-Body-")
            cns:SaveData()
            lastAlias, nativeRequest, nativeMenuBaseline = alias, nil, nil
            log("Native wardrobe owns body | saved CNS body override cleared; mesh retained | " .. alias)
        end)
        if not ok and tostring(reason) ~= lastError then
            lastError = tostring(reason)
            log("Native wardrobe handoff refused | " .. lastError)
        elseif ok then
            lastError = nil
        end
    end)
    log("Native wardrobe equipment-identity handoff registered")
end
return M
