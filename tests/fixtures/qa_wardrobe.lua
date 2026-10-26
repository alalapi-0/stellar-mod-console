-- Independent native-like fixture: saved selection can change before async mesh completion.
local files, callback, cancelled, wall, ticks, calls = {}, nil, false, 1000, 0, 0
local function object(addr) return {IsValid=function() return true end, GetAddress=function() return addr end} end
local function name(s) return {ToString=function() return s end} end
local function map(values)
    return {values=values, Contains=function(self,k) return self.values[k] ~= nil end,
        Find=function(self,k) assert(self.values[k] ~= nil); return {get=function() return self.values[k] end} end,
        ForEach=function(self, fn) for k,v in pairs(self.values) do fn({get=function() return k end},{get=function() return v end}) end end}
end
local world, pawn, pc, movement, body = object(100), object(200), object(400), object(300), object(500)
local oldMesh, newMesh = object(600), object(601)
oldMesh.GetFullName=function() return "SkeletalMesh /Game/Test/Old.Old" end
newMesh.GetFullName=function() return "SkeletalMesh /Game/Test/New.New" end
body.SkeletalMesh, body.bHiddenInGame = oldMesh, false
body.GetOwner=function() return pawn end; body.IsVisible=function() return true end
pawn.Mesh, pawn.CharacterMovement, pawn.MoveInputBlockCount = body, movement, 0
pawn.GetClass=function() return {GetFName=function() return name("CH_P_EVE_01_Blueprint_C") end} end
pawn.GetWorld=function() return world end; pawn.GetController=function() return pc end
pawn.IsPlayerControlled=function() return true end; pawn.IsMoveInputIgnored=function() return false end
pawn.IsMoveInputBlock=function() return false end; pawn.K2_GetActorLocation=function() return {X=0,Y=0,Z=0} end
pawn.GetVelocity=function() return {X=SCENARIO=="moving" and 50 or 0,Y=0,Z=0} end
movement.GetOwner=function() return pawn end; movement.MovementMode=1
pc.Pawn=pawn;pc.GetFName=function() return name("SBNetworkPlayerController_0") end
pc.GetWorld=function() return world end;pc.IsLocalPlayerController=function() return true end
pc.IsMoveInputIgnored=function() return SCENARIO=="locked" end
local saved, cns, data = object(700), object(800), object(900)
saved.GetClass=function() return {GetFName=function() return name("BP_CNS_SaveData_C") end} end
saved.LastActiveOutfitMap=map({["EVE-Body-"]="Known",["EVE-Hair-"]="KeepHair"})
saved.LastOutfitIndexMap=map({Known=1,KeepHair=4});saved.RemovedOutfitMap=map({["EVE-Face-"]=true})
data.UniqueFitID, data.CharacterID, data.FitMeshType, data.MeshSubType, data.NumFitDatas = "Known", "EVE", "Body", "", 2
if SCENARIO:match("^legacy") then
    data.NumFitDatas=0
    data.OutfitPaths={"/Game/Test/New.New","/Game/Test/Old.Old",GetArrayNum=function(self) return #self end}
    if SCENARIO=="legacy-path-mismatch" then data.OutfitPaths[1]="/Game/Test/Wrong.Wrong" end
    if SCENARIO=="legacy-empty" then data.OutfitPaths={GetArrayNum=function() return 0 end} end
end
if SCENARIO=="wrong-character" then data.CharacterID="ADAM" end
cns.GetClass=function() return {GetFullName=function() return "BlueprintGeneratedClass /Game/Mods/DekCNS_P/ModActor.ModActor_C" end} end
cns.GetWorld=function() return world end;cns.SavedModData=saved
cns.OutfitMap=map(SCENARIO=="not-registered" and {} or {Known=data})
if SCENARIO=="cns-menu" then cns.TempMenuUI=object(901);cns.TempMenuUI.IsInViewport=function() return true end end
local pending
cns["Set Custom Outfit"]=function(self,char,mesh,sub,id,index)
    assert(self==cns and char=="EVE" and mesh=="Body" and sub=="" and id=="Known" and index==0)
    calls=calls+1
    if SCENARIO=="native-error" then error("native effect uncertain") end
    saved.LastActiveOutfitMap.values["EVE-Body-"]=id;saved.LastOutfitIndexMap.values[id]=index
    pending=ticks+20
    if SCENARIO=="unrelated-change" then saved.LastActiveOutfitMap.values["EVE-Hair-"]="WrongHair" end
end
FindAllOf=function(cls) if cls=="SBNetworkPlayerController" then return {pc} end;assert(cls=="ModActor_C");return {cns} end
LoopInGameThreadWithDelay=function(ms,fn) assert(ms==20);callback=fn;return 73 end
CancelDelayedAction=function(h) assert(h==73);cancelled=true end
os.time=function() return wall end;os.remove=function(p) files[p]=nil;return true end
os.rename=function(a,b) assert(files[b]==nil);files[b],files[a]=files[a],nil;return true end
io.open=function(p,mode)
    if mode=="rb" then if not files[p] then return end;return {read=function(_,n) return files[p]:sub(1,n) end,close=function() return true end} end
    files[p]="";return {write=function(_,s) files[p]=files[p]..s;return true end,close=function() return true end}
end
local function steps(n)
    for _=1,n do ticks=ticks+1;wall=1000+math.floor(ticks*.02)
        if pending and ticks>=pending and SCENARIO~="wrong-mesh" then body.SkeletalMesh=newMesh;pending=nil end
        if not cancelled then callback() end
    end
end
local nonce="fixture_session_nonce_0123456789"
local control=assert(load(MODULE_SOURCE))().start({directory="owned",nonce=nonce,outfits={Known={"/Game/Test/New.New","/Game/Test/Old.Old"}}})
local function request(id,op,overrides)
    local p={nonce,id,tostring(wall+5),op,"0","0","0","0xc8","0x64"}
    if op=="select" then p[10],p[11]="Known","0" end
    for k,v in pairs(overrides or {}) do p[k]=v end
    files["owned/request.txt"]=table.concat(p,"\t").."\n";steps(5)
end
local function response(id)
    local out={};for k,v in assert(files["owned/"..id..".response.tsv"]):gmatch("([^\t\n]+)\t([^\n]*)\n") do out[k]=v end;return out
end
request("read","wardrobe");assert(response("read").body_id=="Known" and calls==0)
if SCENARIO=="wardrobe" then control.stop();return end
local overrides={}
if SCENARIO=="unknown-outfit" then overrides[10]="Unknown"
elseif SCENARIO=="index-oob" then overrides[11]="2"
elseif SCENARIO=="identity" then overrides[8]="0xffff" end
request("change","select",overrides)
if SCENARIO=="stop-pending" then request("stop","stop");assert(response("stop").reason:find("cannot be cancelled",1,true), response("stop").reason) end
steps(320)
local out=response("change")
local diagnostic="calls="..calls.." reason="..tostring(out.reason).." verified="..tostring(out.selection_verified)
if SCENARIO=="select" or SCENARIO=="legacy-select" then
    assert(calls==1 and out.selection_verified=="true" and out.protected_maps_unchanged=="true",diagnostic)
    assert(out.mesh_path=="/Game/Test/New.New" and out.body_index=="0")
elseif SCENARIO=="wrong-mesh" then assert(calls==1 and out.selection_verified=="false" and out.reason=="wall deadline expired",diagnostic)
elseif SCENARIO=="unrelated-change" then assert(calls==1 and out.selection_verified=="false" and out.reason:find("unrelated saved selection changed",1,true),diagnostic)
elseif SCENARIO=="native-error" then assert(calls==1 and out.selection_verified=="false" and out.reason:find("uncertain native call",1,true),diagnostic)
elseif SCENARIO=="stop-pending" then assert(calls==1 and out.selection_verified=="false")
else assert(calls==0 and out.phase=="rejected",out.reason) end
control.stop();assert(cancelled)
