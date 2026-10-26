local Policy = assert(load(MODULE_SOURCE, "@OwnedLinkedPostProcess.lua"))()
local nextID = 100
local classes = {}
local function class(name, path)
    local key = path or name
    if classes[key] then return classes[key] end
    nextID = nextID + 1
    local c = {id=nextID, name=name, path=path or name, valid=true}
    function c:IsValid() return self.valid end
    function c:GetAddress() return self.id end
    function c:GetFName() return {ToString=function() return self.name end} end
    function c:GetFullName() return self.path end
    classes[key] = c; return c
end
local function object(name, cls)
    nextID = nextID + 1
    local o = {id=nextID, name=name, class=cls, valid=true}
    function o:IsValid() return self.valid end
    function o:GetAddress() return self.id end
    function o:GetFullName() return self.name .. "_" .. self.id end
    function o:GetClass() return self.class end
    return o
end
local MOD = class("ABP_EveProportions_C", "AnimBlueprintGeneratedClass /Game/Mods/EveProportions/ABP_EveProportions.ABP_EveProportions_C")
local PASS = class("ABP_EvePhysicsPassthrough_C", "AnimBlueprintGeneratedClass /Game/Mods/EveProportions/ABP_EvePhysicsPassthrough.ABP_EvePhysicsPassthrough_C")
local MAIN = class("CH_P_EVE_01_AnimBP_New_C")
local CHAR = class("CH_P_EVE_01_Blueprint_C")
local alphaFields = {"CustomAnimAlpha", "CustomAnimAlpha2", "CustomAnimAlpha3", "CustomAnimAlpha_Upper",
    "CustomAnimAlpha2_Upper", "CustomAnimAdditiveAlpha", "CustomAnimAdditiveAlpha_Upper", "CustomAnimByMeshSlotBodyAlpha"}
local function main()
    local a = object("Main", MAIN); a.IsSequenceAnim=false
    for _,n in ipairs(alphaFields) do a[n]=0 end
    function a:IsAnyMontagePlaying() return self.montage == true end
    return a
end
local function parent(cls)
    local p = object("PostProcess", cls or MOD)
    function p:GetLinkedAnimGraphInstanceByTag() return self.child end
    return p
end
local world = object("World", class("World"))
local controller = object("Controller", class("SBNetworkPlayerController"))
function controller:IsLocalPlayerController() return true end
function controller:GetWorld() return world end
local owner = object("Eve", CHAR); controller.Pawn=owner
function owner:IsPlayerControlled() return true end
function owner:GetController() return controller end
function owner:GetWorld() return world end
local body = object("Body", class("SBSkeletalMeshComponent")); owner.Mesh=body
body.owner=owner; body.SkeletalMesh=object("Mesh", class("SkeletalMesh")); body.AnimClass=MAIN
body.AnimScriptInstance=main(); body.PostProcessAnimInstance=parent(); body.calls={}
local array = {}; body.LinkedInstances=array
function array:GetArrayNum()
    if self.readFailure then error("injected registry read failure") end
    return #self
end
function body:GetOwner() return self.owner end
function body:GetAnimationMode() return 0 end
function body:SetAnimClass(value)
    self.calls[#self.calls+1] = value == nil and "clear" or "restore"
    if value == nil then
        for n=#array,1,-1 do array[n].valid=false; array[n]=nil end
        self.AnimClass=nil; self.AnimScriptInstance=nil
        if self.failReadAfterClear then array.readFailure=true end
        if self.failClear then error("injected clear failure after effect") end
    else
        array.readFailure=false; self.AnimClass=value; self.AnimScriptInstance=main()
        self.PostProcessAnimInstance=parent()
    end
end
local policy = Policy.new({tag="OriginalPostProcess"})
local function link(cls)
    local child=object("Child", cls); array[#array+1]=child
    body.PostProcessAnimInstance.child=child
    policy:observe(body, body.PostProcessAnimInstance, true)
    return child
end
local function nextMesh()
    body.SkeletalMesh=object("NextMesh", class("SkeletalMesh"))
    body.PostProcessAnimInstance=parent(class("OriginalPostProcess"))
end
link(class("Original1")); nextMesh()
if SCENARIO == "bounded" or SCENARIO == "missing-policy-negative" then
    for n=1,12 do
        if SCENARIO == "bounded" then assert(policy:beforeRefresh(body, body.PostProcessAnimInstance)) end
        body.PostProcessAnimInstance=parent(); link(class("Original"..n)); nextMesh()
        assert(#array==1, "owned linked registry must remain bounded")
    end
elseif SCENARIO == "foreign" then
    local foreign=object("Foreign", class("ForeignClass")); array[#array+1]=foreign
    local changed,reason=policy:beforeRefresh(body, body.PostProcessAnimInstance)
    assert(not changed and reason=="unowned_link_preserved" and #body.calls==0 and foreign.valid)
elseif SCENARIO == "foreign-private-tag" then
    body.PostProcessAnimInstance=parent(); local foreign=object("Foreign", class("ForeignClass"))
    array[#array+1]=foreign; body.PostProcessAnimInstance.child=foreign
    policy:observe(body, body.PostProcessAnimInstance)
    local changed,reason=policy:beforeRefresh(body, body.PostProcessAnimInstance)
    assert(not changed and reason=="unowned_link_preserved" and #body.calls==0)
elseif SCENARIO == "active-montage" or SCENARIO == "sequence" or SCENARIO == "custom-pose" then
    if SCENARIO=="active-montage" then body.AnimScriptInstance.montage=true end
    if SCENARIO=="sequence" then body.AnimScriptInstance.IsSequenceAnim=true end
    if SCENARIO=="custom-pose" then body.AnimScriptInstance.CustomAnimAlpha=1 end
    local changed,reason=policy:beforeRefresh(body, body.PostProcessAnimInstance)
    assert(not changed and reason=="active_or_unknown_animation_preserved" and #body.calls==0)
elseif SCENARIO == "same-current" then
    body.PostProcessAnimInstance=parent(); array[1].valid=true; body.PostProcessAnimInstance.child=array[1]
    local changed,reason=policy:beforeRefresh(body, body.PostProcessAnimInstance)
    assert(not changed and reason=="no_stale_owned_child" and #body.calls==0)
elseif SCENARIO == "private-default" then
    body.PostProcessAnimInstance=parent(); local child=object("Default", PASS)
    array[#array+1]=child; body.PostProcessAnimInstance.child=child
    assert(policy:beforeRefresh(body, body.PostProcessAnimInstance)); assert(#array==0)
elseif SCENARIO == "new-world" then
    world=object("NewWorld", class("World"))
    local changed,reason=policy:beforeRefresh(body, body.PostProcessAnimInstance)
    assert(not changed and reason=="unowned_link_preserved" and #body.calls==0)
elseif SCENARIO == "clear-error" or SCENARIO == "read-error" then
    body.failClear=SCENARIO=="clear-error"; body.failReadAfterClear=SCENARIO=="read-error"
    local ok=pcall(function() policy:beforeRefresh(body, body.PostProcessAnimInstance) end)
    assert(not ok and #body.calls==2 and body.calls[1]=="clear" and body.calls[2]=="restore")
    assert(body.AnimClass==MAIN and body.AnimScriptInstance:IsValid(), "paired restoration required after failure")
else error("unknown scenario "..SCENARIO) end
