-- Native-like callback fixture; verifies ownership, refusal and uncertain effects.
local files, callback, calls, cancelled = {}, nil, 0, false
local function name(s) return {ToString=function() return s end} end
local function object(addr, cls)
    return {IsValid=function() return true end, GetAddress=function() return addr end,
        GetClass=function() return {GetFName=function() return name(cls) end} end}
end
local function map(values)
    return {values=values, Contains=function(self,k) return self.values[k]~=nil end,
        Find=function(self,k) assert(self.values[k]~=nil);return {get=function() return self.values[k] end} end,
        ForEach=function(self,fn) for k,v in pairs(self.values) do
            fn({get=function() return k end},{get=function() return v end}) end end}
end
local world, pawn, pc, movement = object(100), object(200,"CH_P_EVE_01_Blueprint_C"), object(400), object(300)
local body, mesh = object(500), object(600)
mesh.GetFullName=function() return "SkeletalMesh /Game/Test/Old.Old" end
body.SkeletalMesh=mesh;body.GetOwner=function() return pawn end;body.IsVisible=function() return true end
pawn.Mesh=body;pawn.CharacterMovement=movement;pawn.MoveInputBlockCount=SCENARIO=="additional-lock" and 1 or 0
pawn.GetWorld=function() return world end;pawn.GetController=function() return pc end
pawn.IsPlayerControlled=function() return true end;pawn.IsMoveInputIgnored=function() return false end
pawn.IsMoveInputBlock=function() return pawn.MoveInputBlockCount~=0 end
pawn.K2_GetActorLocation=function() return {X=0,Y=0,Z=0} end
pawn.GetVelocity=function() return {X=SCENARIO=="moving" and 10 or 0,Y=0,Z=0} end
movement.GetOwner=function() return pawn end;movement.MovementMode=1
pc.Pawn=pawn;pc.GetFName=function() return name("SBNetworkPlayerController_0") end
pc.GetWorld=function() return world end;pc.IsLocalPlayerController=function() return true end
pc.IsMoveInputIgnored=function() return SCENARIO=="native-ignored" end
local saved,cns,menu=object(700,"BP_CNS_SaveData_C"),object(800),object(900,"WB_CustomNanoSootUI_C")
saved.LastActiveOutfitMap=map({["EVE-Body-"]="Known",["EVE-Hair-"]="KeepHair"})
saved.LastOutfitIndexMap=map({Known=0,KeepHair=4,[""]=0});saved.RemovedOutfitMap=map({["EVE-Face-"]=true})
cns.GetClass=function() return {GetFullName=function() return "BlueprintGeneratedClass /Game/Mods/DekCNS_P/ModActor.ModActor_C" end} end
cns.GetWorld=function() return world end;cns.SavedModData=saved;cns.TempMenuUI=menu
local registered=object(801)
registered.UniqueFitID="Known";registered.CharacterID=SCENARIO=="wrong-registration" and "ADAM" or "EVE"
registered.FitMeshType="Body";registered.MeshSubType="";cns.OutfitMap=map({Known=registered})
menu.ModActor=cns;menu.IsInViewport=function() return SCENARIO~="closed-menu" end
menu.IsHiddenUI=SCENARIO=="hidden-menu"
menu.ConfigurablesArea=object(901);menu.ConfigurablesArea.IsVisible=function() return SCENARIO~="closed-config" end
menu.LastClickedOutfitButton=object(902);menu.LastClickedOutfitButton.ButtonID=SCENARIO=="wrong-config" and "Other" or "Known"
local toggle=SCENARIO=="toggle" or SCENARIO=="toggle-invalid"
local cls=toggle and "WB_ToggleButton_C" or "WB_SliderButton_C"
local dc=toggle and "BP_CNS_UserConfigData_MaterialToggle_C" or "BP_CNS_UserConfigData_ScalarControls_C"
local widget,data,tree=object(1000,cls),object(1001,dc),object(1002)
widget.GetOuter=function() return tree end
tree.GetOuter=function() return SCENARIO=="wrong-widget-owner" and object(1999) or menu end
widget.BaseConfigData=data;data.DisplayName="Target";data.MaterialIndex=18;data.ControlledBy=""
data.ParamName="EmissivePower";data.Association="Global";data.LayerIndex=-1;data.Min=0;data.Max=2
data.Value=0;if toggle then data.Value=false end
local spec={kind=toggle and "toggle" or "scalar",display="Target",material=18,param="EmissivePower",min=0,max=2}
if SCENARIO=="dependent" then data.ControlledBy="Other" end
if SCENARIO=="metadata" then data.ParamName="Wrong" end
if SCENARIO=="changed-bounds" then data.Max=3 end
if SCENARIO=="association" then data.Association="Layer" end
if SCENARIO=="wrong-menu-owner" then menu.ModActor=object(8888) end
if SCENARIO=="wrong-mesh" then mesh.GetFullName=function() return "SkeletalMesh /Game/Test/Wrong.Wrong" end end
if SCENARIO=="unsupported" then spec.kind="vector" end
local function native(self,w,value)
    assert(self==menu and w==widget);calls=calls+1
    if SCENARIO~="readback-mismatch" then data.Value=value end
    if SCENARIO=="unrelated-change" then saved.LastActiveOutfitMap.values["EVE-Hair-"]="WrongHair" end
    if SCENARIO=="body-removed" then saved.RemovedOutfitMap.values["EVE-Body-"]=true end
    if SCENARIO=="empty-index-change" then saved.LastOutfitIndexMap.values[""]=1 end
    if SCENARIO=="native-error" then error("native effect uncertain") end
end
menu.OnOutfitConfigurationScalarChanged=native;menu.OnOutfitConfigurationMaterialToggleChanged=native
FindAllOf=function(kind)
    if kind=="SBNetworkPlayerController" then return {pc} end
    if kind=="ModActor_C" then return {cns} end
    assert(kind==cls,kind)
    if SCENARIO=="missing-widget" then return {} end
    if SCENARIO=="duplicate-widget" then return {widget,widget} end
    return {widget}
end
LoopInGameThreadWithDelay=function(ms,fn) assert(ms==20);callback=fn;return 75 end
CancelDelayedAction=function(h) assert(h==75);cancelled=true end
os.time=function() return 1000 end;os.remove=function(p) files[p]=nil;return true end
os.rename=function(a,b) assert(files[b]==nil);files[b],files[a]=files[a],nil;return true end
io.open=function(p,mode)
    if mode=="rb" then if not files[p] then return end;return {read=function(_,n) return files[p]:sub(1,n) end,close=function() return true end} end
    files[p]="";return {write=function(_,s) files[p]=files[p]..s;return true end,close=function() return true end}
end
local nonce="fixture_control_nonce_0123456789"
local controls={Known={target=spec}}
if SCENARIO=="unlisted" then controls={} end
local module=assert(load(MODULE_SOURCE))().start({directory="owned",nonce=nonce,outfits={Known={"/Game/Test/Old.Old"}},controls=controls})
local parts={nonce,"change","1005","control","0","0","0","0xc8","0x64","Known","target",toggle and "1" or "2"}
if SCENARIO=="identity" then parts[8]="0xffff" end
if SCENARIO=="range" then parts[12]="2.1" end
if SCENARIO=="nan" then parts[12]="nan" end
if SCENARIO=="toggle-invalid" then parts[12]="2" end
files["owned/request.txt"]=table.concat(parts,"\t").."\n"
for _=1,10 do callback() end
local out={};for k,v in assert(files["owned/change.response.tsv"]):gmatch("([^\t\n]+)\t([^\n]*)\n") do out[k]=v end
local diagnostic="calls="..calls.." reason="..tostring(out.reason).." verified="..tostring(out.control_data_verified)
if SCENARIO=="scalar" or SCENARIO=="toggle" then
    assert(calls==1 and out.control_data_verified=="true" and out.protected_maps_unchanged=="true",diagnostic)
elseif SCENARIO=="native-error" then
    assert(calls==1 and out.phase=="completed" and out.control_data_verified=="false" and out.reason:find("uncertain native control call",1,true),diagnostic)
elseif SCENARIO=="readback-mismatch" then assert(calls==1 and out.control_data_verified=="false",diagnostic)
elseif SCENARIO=="unrelated-change" or SCENARIO=="body-removed" or SCENARIO=="empty-index-change" then
    assert(calls==1 and out.protected_maps_unchanged=="false",diagnostic)
else assert(calls==0 and out.phase=="rejected",diagnostic) end
local previous=calls;for _=1,15 do callback() end;assert(calls==previous,"replayed effect")
module.stop();assert(cancelled)
