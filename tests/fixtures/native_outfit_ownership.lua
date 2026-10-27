-- Synthetic UObject/TArray fixture. No real game-thread, widget or save I/O.
-- Native slot12=None is Lua index13; native Body slot13 is Lua index14.
local tick = nil
local nativeEvents, nativeFunctionReady, registrationAttempts = {}, {}, {}
local panelNames = {"WB_Equip_MainTab", "WB_Equip_Set", "WB_Equip_SelectDetail"}
local panelPaths = {}
for _, name in ipairs(panelNames) do
    panelPaths[name] = "/Game/Art/UI/Widget/GameMenu/Equip/" .. name .. "." .. name .. "_C:EventNotification"
    nativeFunctionReady[panelPaths[name]] = false
end
local function registrationCount()
    local count = 0
    for _, n in pairs(registrationAttempts) do count = count + n end
    return count
end
local calls, removedCalls, order, messages = {}, {}, {}, {}
local cnsSelectionCallback = nil
local cnsSelectionPath = "/Game/Mods/DekCNS_P/ModActor.ModActor_C:Set Custom Outfit"
local alias, address = "BS_Green_Suit", 10
local cnsValid, savedValid, nativeMenuVisible, cnsMenuOpen = true, true, true, false
local activeOutfits = {["EVE-Body-"]="CustomBody", ["EVE-Hair-"]="KeepHair", ["EVE-Ears-"]="KeepEars", ["LILY-Body-"]="KeepLily"}
local removedOutfits = {["EVE-Body-"]=true, ["EVE-Hair-"]=true, ["EVE-Ears-"]=false, ["LILY-Body-"]=true}
local outfitIndices = {["EVE-Body-"]=2, ["EVE-Hair-"]=3, ["LILY-Body-"]=4}
outfitIndices.CustomBody = 2
outfitIndices.Contains = function(self, key) return self[key] ~= nil end
outfitIndices.Find = function(self, key) return {get=function() return self[key] end} end
local presets = {Name="KeepPreset", Values={Body="SavedPresetBody", Hair="SavedPresetHair"}}
local liveMesh = "NativeBodyMesh"
local world = {IsValid=function() return true end, GetAddress=function() return 100 end}
local function cls(name, full)
    return {
        GetFName=function() return {ToString=function() return name end} end,
        GetFullName=function() return full or name end,
    }
end
local equipment = {
    [13]={ToString=function() return "None" end},
    [14]={ToString=function() return alias end},
    GetArrayNum=function() return 14 end,
}
local pawn = {
    IsValid=function() return true end,
    GetAddress=function() return address end,
    GetClass=function() return cls("CH_P_EVE_01_Blueprint_C") end,
    EquipmentDataList=equipment,
    MoveInputBlockCount=1,
}
local pc = {
    IsValid=function() return true end,
    IsLocalPlayerController=function() return true end,
    GetWorld=function() return world end,
    Pawn=pawn,
}
local saved = {
    IsValid=function() return savedValid end,
    GetClass=function() return cls("BP_CNS_SaveData_C") end,
    LastActiveOutfitMap={
        Remove=function(self, key)
            assert(key == "EVE-Body-", "only the EVE Body override key may be removed")
            assert(savedValid, "invalid saved data must not be mutated")
            calls[#calls + 1] = key
            order[#order + 1] = "remove-override"
            activeOutfits[key] = nil -- absence, not the CNS explicit-removal value ""
        end,
        Contains=function(self, key) return activeOutfits[key] ~= nil end,
        Find=function(self, key) return {get=function() return activeOutfits[key] end} end,
    },
    RemovedOutfitMap={
        Remove=function(self, key)
            assert(key == "EVE-Body-", "only the EVE Body removal marker may be removed")
            assert(savedValid, "invalid saved data must not be mutated")
            removedCalls[#removedCalls + 1] = key
            order[#order + 1] = "remove-marker"
            removedOutfits[key] = nil
        end,
        Contains=function(self, key) return removedOutfits[key] ~= nil end,
    },
    LastOutfitIndexMap=outfitIndices,
    Presets=presets,
    UpdateActiveOutfit=function() error("saved UpdateActiveOutfit must never be called") end,
}
local cns = {
    IsValid=function() return cnsValid end,
    GetAddress=function() return 80 end,
    GetWorld=function() return world end,
    GetClass=function()
        return cls("ModActor_C", "BlueprintGeneratedClass /Game/Mods/DekCNS_P/ModActor.ModActor_C")
    end,
    TempMenuUI={IsValid=function() return true end, IsInViewport=function() return cnsMenuOpen end},
    SavedModData=saved,
    UpdateActiveOutfit=function() error("Actor deselection API must never be called") end,
    SaveData=function()
        assert(order[#order] == "remove-marker", "save must follow removal of both Body keys")
        assert(activeOutfits["EVE-Body-"] == nil and removedOutfits["EVE-Body-"] == nil,
            "missing override and missing removal marker are required before saving")
        order[#order + 1] = "save"
    end,
}
local nativeWidget = {
    IsValid=function() return true end,
    IsVisible=function() return nativeMenuVisible end,
}
local function equipmentMenuEvent(panel, eventName)
    panel = panel or "WB_Equip_MainTab"
    eventName = eventName or "EquipedDetail"
    local callback = nativeEvents[panelPaths[panel]]
    assert(type(callback) == "function", "the selected panel must have its own registered callback")
    local value = eventName
    if panel ~= "WB_Equip_MainTab" then
        value = setmetatable({ToString=function() return eventName end},
            {__tostring=function() error("FString events require ToString") end})
    end
    callback({get=function() return nativeWidget end}, {get=function() return value end})
end
local function unchanged(expectedBody)
    assert(activeOutfits["EVE-Body-"] == expectedBody, "Body must have expected value or missing key")
    assert(activeOutfits["EVE-Hair-"] == "KeepHair" and activeOutfits["EVE-Ears-"] == "KeepEars"
        and activeOutfits["LILY-Body-"] == "KeepLily", "all other override keys must be retained")
    assert(removedOutfits["EVE-Hair-"] == true and removedOutfits["EVE-Ears-"] == false
        and removedOutfits["LILY-Body-"] == true, "all other removal markers must be retained")
    assert(outfitIndices["EVE-Body-"] == 2 and outfitIndices["EVE-Hair-"] == 3
        and outfitIndices["LILY-Body-"] == 4, "LastOutfitIndexMap must remain untouched")
    assert(presets.Name == "KeepPreset" and presets.Values.Body == "SavedPresetBody"
        and presets.Values.Hair == "SavedPresetHair", "presets must remain untouched")
    assert(liveMesh == "NativeBodyMesh", "live native mesh must remain untouched")
end
local function successfulHandoffs(expected)
    assert(#calls == expected, "explicit native change must remove Body override exactly once")
    assert(#removedCalls == expected, "Body removal marker must be removed exactly once per handoff")
    assert(#order == 3 * expected, "both Body key removals must be saved exactly once")
    for i = 1, #order, 3 do
        assert(order[i] == "remove-override" and order[i + 1] == "remove-marker"
            and order[i + 2] == "save", "saved API order")
    end
    assert(removedOutfits["EVE-Body-"] == (expected == 0 and true or nil),
        "native ownership requires absence of the Body removal marker")
    unchanged(expected == 0 and "CustomBody" or nil)
end
FindAllOf=function(name)
    assert(name == "SBNetworkPlayerController")
    return {pc}
end
StaticFindObject=function(path)
    if path == cnsSelectionPath then return {IsValid=function() return cnsValid end} end
    assert(nativeFunctionReady[path] ~= nil, "only the exact three equipment panel paths may be queried")
    return {IsValid=function() return nativeFunctionReady[path] end}
end
RegisterHook=function(path, callback)
    if path == cnsSelectionPath then
        assert(not cnsSelectionCallback, "only one explicit CNS selection observer")
        cnsSelectionCallback = callback
        return
    end
    assert(nativeFunctionReady[path] ~= nil, "only an exact equipment panel may be hooked")
    registrationAttempts[path] = (registrationAttempts[path] or 0) + 1
    assert(nativeFunctionReady[path], "unloaded menu UFunction must not be hooked")
    assert(nativeEvents[path] == nil, "each native panel gets exactly one observer")
    nativeEvents[path] = callback
end
LoopInGameThreadWithDelay=function(delay, callback)
    assert(delay == 500)
    tick = callback
end
local module = assert(load(MODULE_SOURCE, "@NativeOutfitOwnership.lua"))()
module.start(function() return cns end, function(message) messages[#messages + 1] = message end)
assert(type(tick) == "function", "baseline observer must start before the menu is loaded")
assert(registrationCount() == 0 and next(nativeEvents) == nil, "menu UFunctions are initially unavailable")
tick()
successfulHandoffs(0)
assert(registrationCount() == 0, "missing menu must not trigger RegisterHook")
if SCENARIO == "load-order" then
    local baselineObserved = false
    for _, message in ipairs(messages) do
        if message:find("Native wardrobe baseline | BS_Green_Suit", 1, true) then baselineObserved = true end
        assert(not message:find("handoff refused", 1, true), "unloaded menu must not interrupt observation")
    end
    assert(baselineObserved, "unloaded menu must not interrupt the Pawn baseline")
    alias = "BS_01"
    tick()
    successfulHandoffs(0)
end
if SCENARIO == "partial-load-order" then
    nativeFunctionReady[panelPaths.WB_Equip_MainTab] = true
else
    for _, path in pairs(panelPaths) do nativeFunctionReady[path] = true end
end
tick()
local readyCount = SCENARIO == "partial-load-order" and 1 or 3
assert(registrationCount() == readyCount, "each ready menu registers independently")
assert(type(nativeEvents[panelPaths.WB_Equip_MainTab]) == "function", "main menu callback is ready")
tick()
tick()
assert(registrationCount() == readyCount, "later ticks must not register duplicate menu hooks")
successfulHandoffs(0)

if SCENARIO == "load-order" then
    equipmentMenuEvent()
    alias = "BS_09"
    tick()
    successfulHandoffs(1)
elseif SCENARIO == "cold-initialization" then
    alias = "BS_01"
    tick()
    alias = "BS_09"
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "explicit-native-change" or SCENARIO == "child-set-event" or SCENARIO == "child-detail-event" then
    local panel = SCENARIO == "child-set-event" and "WB_Equip_Set"
        or SCENARIO == "child-detail-event" and "WB_Equip_SelectDetail" or "WB_Equip_MainTab"
    equipmentMenuEvent(panel)
    alias = "BS_01"
    tick()
    successfulHandoffs(1)
    local eventLogged = false
    for _, message in ipairs(messages) do
        assert(not message:find("menu event refused", 1, true), "native event payload must be read safely")
        if message == "Native wardrobe menu event | EquipedDetail" then eventLogged = true end
    end
    assert(eventLogged, "string and FString event labels must be logged as their text")
elseif SCENARIO == "partial-load-order" then
    equipmentMenuEvent()
    alias = "BS_01"
    tick()
    successfulHandoffs(1)
    assert(registrationAttempts[panelPaths.WB_Equip_Set] == nil
        and registrationAttempts[panelPaths.WB_Equip_SelectDetail] == nil,
        "unloaded child panels must not block the ready main panel")
    nativeFunctionReady[panelPaths.WB_Equip_Set] = true
    tick()
    tick()
    assert(registrationCount() == 2 and registrationAttempts[panelPaths.WB_Equip_MainTab] == 1
        and registrationAttempts[panelPaths.WB_Equip_Set] == 1,
        "newly loaded child registers once without re-registering ready paths")
    nativeFunctionReady[panelPaths.WB_Equip_SelectDetail] = true
    tick()
    tick()
    assert(registrationCount() == 3, "the final child registers independently once")
    for _, path in pairs(panelPaths) do assert(registrationAttempts[path] == 1, "one hook per path") end
elseif SCENARIO == "refresh-event" or SCENARIO == "show-detail-event" then
    local eventName = SCENARIO == "refresh-event" and "Refresh" or "ShowDetail"
    equipmentMenuEvent("WB_Equip_SelectDetail", eventName)
    alias = "BS_01"
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "newer-cns-choice" then
    equipmentMenuEvent()
    alias, cnsMenuOpen, activeOutfits["EVE-Body-"] = "BS_01", true, "NewCNS"
    removedOutfits["EVE-Body-"] = nil -- a real CNS reselection clears its Body removal marker
    tick()
    cnsMenuOpen = false
    tick()
    alias = "BS_29"
    tick()
    assert(#calls == 0 and #order == 0, "newer CNS choice must cancel pending native ownership")
    unchanged("NewCNS")
    assert(removedOutfits["EVE-Body-"] == nil, "the newer CNS choice owns its removal marker")
elseif SCENARIO == "new-pawn" then
    equipmentMenuEvent()
    address, alias = 11, "BS_01"
    tick()
    alias = "BS_29"
    tick()
    successfulHandoffs(0)
    equipmentMenuEvent()
    alias = "BS_09"
    tick()
    successfulHandoffs(1)
elseif SCENARIO == "invalid-saved-data" then
    equipmentMenuEvent()
    alias, savedValid = "BS_01", false
    tick()
    successfulHandoffs(0)
    savedValid = true
    tick()
    successfulHandoffs(1)
elseif SCENARIO == "unchanged-alias" then
    equipmentMenuEvent()
    tick()
    successfulHandoffs(0)
    alias = "BS_01"
    tick()
    tick()
    equipmentMenuEvent()
    tick()
    successfulHandoffs(1)
elseif SCENARIO == "non-body-alias" then
    equipmentMenuEvent()
    alias = "Hair_Default"
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "hidden-native-menu" then
    nativeMenuVisible = false
    equipmentMenuEvent()
    alias = "BS_01"
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "event-after-passive-change" or SCENARIO == "expired-no-event" then
    equipmentMenuEvent()
    for _ = 1, 12 do tick() end
    alias = "BS_01"
    tick() -- equipment changes before its post-action notification
    successfulHandoffs(0) -- the expired request must never grant ownership
    if SCENARIO == "event-after-passive-change" then
        equipmentMenuEvent("WB_Equip_SelectDetail")
        tick()
        successfulHandoffs(1)
    end
elseif SCENARIO == "menu-entry-after-passive-change" then
    alias = "BS_01"
    tick() -- passive initialization before the user opens the menu
    equipmentMenuEvent()
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "closed-menu-baseline" then
    equipmentMenuEvent()
    for _ = 1, 12 do tick() end
    nativeMenuVisible, pawn.MoveInputBlockCount = false, 0
    tick()
    alias = "BS_01"
    tick()
    nativeMenuVisible = true
    equipmentMenuEvent()
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "native-action-close-before-poll" then
    equipmentMenuEvent()
    alias, nativeMenuVisible, pawn.MoveInputBlockCount = "BS_01", false, 0
    tick()
    successfulHandoffs(1)
elseif SCENARIO == "native-panel-rebuilt" then
    equipmentMenuEvent()
    for _ = 1, 12 do tick() end
    nativeMenuVisible = false -- the old child hides; native input remains locked
    tick()
    nativeWidget = {IsValid=function() return true end, IsVisible=function() return true end}
    alias = "BS_01"
    tick() -- alias becomes visible before the replacement panel's notification
    successfulHandoffs(0)
    equipmentMenuEvent("WB_Equip_SelectDetail")
    tick()
    successfulHandoffs(1)
elseif SCENARIO == "newer-cns-index" then
    equipmentMenuEvent()
    alias, outfitIndices.CustomBody = "BS_01", 3
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "invalid-native-input-lock" then
    equipmentMenuEvent()
    alias, pawn.MoveInputBlockCount = "BS_01", -1
    tick()
    successfulHandoffs(0)
elseif SCENARIO == "newer-cns-same-selection" or SCENARIO == "cns-other-slot" then
    equipmentMenuEvent()
    alias = "BS_01"
    assert(type(cnsSelectionCallback) == "function", "explicit CNS API must have an observer")
    local function param(value) return {get=function() return value end} end
    cnsSelectionCallback(param(cns), param("EVE"),
        param(SCENARIO == "cns-other-slot" and "Hair" or "Body"), param(""))
    tick()
    successfulHandoffs(SCENARIO == "cns-other-slot" and 1 or 0)
else
    error("Unknown fixture scenario: " .. tostring(SCENARIO))
end
