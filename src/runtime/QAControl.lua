-- Session-only test transport. It never executes request text as Lua.
-- Bootstrap owns directory/nonce. Requests are one atomic request.txt, nine TSV fields:
-- nonce,id,expires_unix,op,duration_ms,world_dx,world_dy,pawn,world.
-- select appends outfit_id,index; wardrobe reads the current EVE Body state.
-- control appends outfit_id,bootstrap_control_key,value for original CNS UI callbacks.
-- move requires addresses returned by observe; direction is one world cardinal.
-- Replies are <id>.response.tsv, one key/value per line, atomically replaced.
local M = {}
local VERSION, INTERVAL, LEASE = "4.2.2", 20, 1200
local function finite(n) return type(n) == "number" and n == n and math.abs(n) < math.huge end
local function live(o) return o ~= nil and o:IsValid() end
local function address(o) return string.format("0x%x", o:GetAddress()) end
local function vector(v)
    local x, y, z = tonumber(v.X), tonumber(v.Y), tonumber(v.Z)
    assert(finite(x) and finite(y) and finite(z), "invalid vector")
    return x, y, z
end
local function clean(v) return tostring(v):gsub("[\r\n\t]", " "):sub(1, 240) end

function M.start(options)
    assert(type(options) == "table" and type(options.directory) == "string", "bootstrap directory required")
    assert(type(options.nonce) == "string" and options.nonce:match("^[a-zA-Z0-9_-]+$")
        and #options.nonce >= 24 and #options.nonce <= 128, "session nonce required")
    assert(type(LoopInGameThreadWithDelay) == "function" and type(CancelDelayedAction) == "function",
        "cancellable game-thread timer required")
    local base = options.directory:gsub("[/\\]+$", "") .. "/"
    local nonce, born = options.nonce, os.time()
    local active, handle, closed, pollTicks, count = nil, nil, false, 0, 0
    local seen = {}

    local function reply(id, fields)
        fields.version, fields.nonce, fields.id = VERSION, nonce, id
        local lines = {}
        for k, v in pairs(fields) do lines[#lines + 1] = k .. "\t" .. clean(v) .. "\n" end
        table.sort(lines)
        -- Windows rename does not replace a destination. Each request has one
        -- acceptance file and one terminal file, neither overwritten/replayed.
        local path = base .. id .. (fields.phase == "active" and ".active.tsv" or ".response.tsv")
        local f = assert(io.open(path .. ".tmp", "wb"))
        assert(f:write(table.concat(lines))); assert(f:close())
        assert(os.rename(path .. ".tmp", path))
    end
    local function resolve()
        local selected
        for _, pc in ipairs(FindAllOf("SBNetworkPlayerController") or {}) do
            if live(pc) and not pc:GetFName():ToString():match("^Default__") and pc:IsLocalPlayerController() then
                assert(not selected, "ambiguous local controller")
                selected = pc
            end
        end
        assert(selected, "local controller unavailable")
        local pawn, world = selected.Pawn, selected:GetWorld()
        assert(live(pawn) and live(world), "pawn/world unavailable")
        assert(pawn:GetClass():GetFName():ToString() == "CH_P_EVE_01_Blueprint_C", "unexpected pawn")
        assert(pawn:IsPlayerControlled() and address(pawn:GetController()) == address(selected),
            "pawn/controller backlink mismatch")
        assert(address(pawn:GetWorld()) == address(world), "pawn/controller world mismatch")
        local move = pawn.CharacterMovement
        assert(live(move), "movement component unavailable")
        assert(address(move:GetOwner()) == address(pawn), "movement owner mismatch")
        return {pc=selected, pawn=pawn, world=world, move=move, pawn_id=address(pawn), world_id=address(world)}
    end
    local function sample(c)
        assert(live(c.pc) and live(c.pawn) and live(c.world) and live(c.move), "object expired")
        assert(c.pc:IsLocalPlayerController() and live(c.pc.Pawn) and address(c.pc.Pawn) == c.pawn_id,
            "pawn changed")
        assert(address(c.pc:GetWorld()) == c.world_id and address(c.pawn:GetWorld()) == c.world_id,
            "world changed")
        assert(address(c.pawn.CharacterMovement) == address(c.move), "movement component changed")
        local x, y, z = vector(c.pawn:K2_GetActorLocation())
        -- Deliberately call Actor.GetVelocity on Pawn, never the same-name SB movement float function.
        local vx, vy, vz = vector(c.pawn:GetVelocity())
        local blocked, mode = tonumber(c.pawn.MoveInputBlockCount), tonumber(c.move.MovementMode)
        assert(finite(blocked) and blocked >= 0 and finite(mode), "lock/mode unavailable")
        local ignored = c.pc:IsMoveInputIgnored() or c.pawn:IsMoveInputIgnored() or c.pawn:IsMoveInputBlock()
        return {pawn=c.pawn_id, world=c.world_id, x=x, y=y, z=z, vx=vx, vy=vy, vz=vz,
            speed=math.sqrt(vx*vx + vy*vy), blocked=blocked, ignored=ignored, mode=mode}
    end
    local function clock(c)
        local gs = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        assert(live(gs), "GameplayStatics unavailable")
        local t = tonumber(gs:GetRealTimeSeconds(c.world))
        assert(finite(t) and t >= 0, "real clock unavailable")
        return t
    end
    local function scalarText(v)
        if type(v) == "string" then return v end
        if type(v) == "number" or type(v) == "boolean" then return tostring(v) end
        return v:ToString()
    end
    local function cnsOwnsInput(c)
        for _, actor in ipairs(FindAllOf("ModActor_C") or {}) do
            if live(actor) and actor:GetClass():GetFullName():find("/Game/Mods/DekCNS_P/ModActor", 1, true) then
                local world, menu = actor:GetWorld(), actor.TempMenuUI
                if live(world) and address(world) == c.world_id and live(menu) and menu:IsInViewport() then
                    return true -- CNS can capture camera/input without a native movement lock.
                end
            end
        end
        return false
    end
    local function wardrobe(c)
        local cns
        for _, actor in ipairs(FindAllOf("ModActor_C") or {}) do
            if live(actor) and actor:GetClass():GetFullName():find("/Game/Mods/DekCNS_P/ModActor", 1, true)
                and address(actor:GetWorld()) == c.world_id then
                assert(not cns, "ambiguous CNS actor")
                cns = actor
            end
        end
        assert(cns and live(cns.SavedModData), "current CNS saved data unavailable")
        local saved, key = cns.SavedModData, "EVE-Body-"
        assert(saved:GetClass():GetFName():ToString() == "BP_CNS_SaveData_C", "unexpected CNS saved data")
        local id = saved.LastActiveOutfitMap:Contains(key) and scalarText(saved.LastActiveOutfitMap:Find(key):get()) or ""
        local index = id ~= "" and saved.LastOutfitIndexMap:Contains(id)
            and tonumber(saved.LastOutfitIndexMap:Find(id):get()) or -1
        local component = c.pawn.Mesh -- native ACharacter body slot; CNS converts Body to Mesh
        assert(live(component) and address(component:GetOwner()) == c.pawn_id, "current Body component unavailable")
        local mesh = component.SkeletalMesh
        assert(live(mesh), "current Body mesh unavailable")
        return cns, saved, {body_id=id, body_index=index, body_component=address(component),
            mesh_address=address(mesh), mesh_path=assert(mesh:GetFullName():match("^%S+ (.+)$")),
            body_visible=component:IsVisible() and not component.bHiddenInGame}
    end
    local function protected(saved, target)
        local out = {}
        for _, name in ipairs({"LastActiveOutfitMap", "LastOutfitIndexMap", "RemovedOutfitMap"}) do
            saved[name]:ForEach(function(k, v)
                local key = scalarText(k:get())
                if not (name == "LastOutfitIndexMap" and key == target)
                    and not (name ~= "LastOutfitIndexMap" and key == "EVE-Body-") then
                    out[#out + 1] = name .. "\0" .. key .. "\0" .. scalarText(v:get())
                end
            end)
        end
        table.sort(out)
        return table.concat(out, "\n")
    end
    local function finish(reason, state)
        local a = active
        active = nil -- no more submissions even if response I/O fails
        local out = state or {}
        if a.kind == "select" then
            out.op, out.phase, out.reason = "select", "completed", reason
            out.requested_outfit, out.requested_index, out.native_calls = a.target, a.index, a.calls
            out.before_body_id, out.before_body_index = a.body_before.body_id, a.body_before.body_index
            out.selection_verified = reason == "selection matched saved data and live mesh"
            reply(a.id, out)
            return
        end
        out.op, out.phase, out.reason = "move", "completed", reason
        out.before_x, out.before_y, out.before_z = a.before.x, a.before.y, a.before.z
        out.peak_speed, out.submissions = a.peak, a.submissions
        out.duration_ms = a.duration
        out.input_submitted = a.submissions > 0
        if state then
            out.horizontal_displacement = math.sqrt((state.x-a.before.x)^2 + (state.y-a.before.y)^2)
            out.settled = state.speed <= 5
            out.movement_observed = out.horizontal_displacement > 0
        end
        reply(a.id, out)
    end
    local function shutdown(reason)
        if active then finish(reason) end
        closed = true
        if handle ~= nil then CancelDelayedAction(handle) end
    end
    local function readRequest()
        local f = io.open(base .. "request.txt", "rb")
        if not f then return end
        local text = f:read(1025); f:close()
        if not text or #text > 1024 then return end
        text = text:gsub("\r?\n$", "")
        if text:find("[\r\n]") then return end
        local parts = {}
        for part in (text .. "\t"):gmatch("([^\t]*)\t") do parts[#parts + 1] = part end
        if (#parts ~= 9 and #parts ~= 11 and #parts ~= 12) or parts[1] ~= nonce or not parts[2]:match("^[a-zA-Z0-9_-]+$")
            or #parts[2] > 64 or seen[parts[2]] then return end
        local id, deadline, op = parts[2], tonumber(parts[3]), parts[4]
        seen[id], count = true, count + 1
        if count > 200 then shutdown("request limit"); return end
        local now = os.time()
        if not finite(deadline) or deadline % 1 ~= 0 or deadline < now or deadline > now + 5 then
            reply(id, {op=op, phase="rejected", reason="expired or excessive deadline"}); return
        end
        if op == "stop" then
            local selecting = active and active.kind == "select"
            if active then finish(selecting and "tracking stopped; dispatched CNS call may finish" or "stopped") end
            reply(id, {op=op, phase="completed", reason=selecting
                and "tracking stopped; CNS call cannot be cancelled" or "input submissions stopped"}); return
        end
        if op ~= "observe" and op ~= "move" and op ~= "wardrobe" and op ~= "select" and op ~= "control" then
            reply(id, {op=op, phase="rejected", reason="unknown operation"}); return
        end
        if active then reply(id, {op=op, phase="rejected", reason="move busy"}); return end
        local dispatchedControl
        local ok, reason = pcall(function()
            local c = resolve()
            local state = sample(c)
            if op == "wardrobe" or op == "select" or op == "control" then
                local cns, saved, body = wardrobe(c)
                for k, v in pairs(body) do state[k] = v end
                if op == "wardrobe" then
                    state.op, state.phase = op, "completed"
                    reply(id, state); return
                end
                if op == "control" then
                    assert(#parts == 12 and parts[5] == "0" and parts[6] == "0" and parts[7] == "0",
                        "control requires outfit, control key and value")
                    assert(parts[8] == c.pawn_id and parts[9] == c.world_id, "observed identity required")
                    assert(state.mode == 1 and state.speed <= 5 and state.blocked == 0 and not state.ignored,
                        "control requires stationary walking without another native movement lock")
                    local target, key, value = parts[10], parts[11], tonumber(parts[12])
                    local allowed = type(options.outfits) == "table" and options.outfits[target]
                    assert(body.body_id == target and body.body_visible and type(allowed) == "table"
                        and allowed[body.body_index + 1] == body.mesh_path, "control body outside allowlist")
                    assert(cns.OutfitMap:Contains(target), "control outfit not registered")
                    local registered = cns.OutfitMap:Find(target):get()
                    assert(live(registered) and scalarText(registered.UniqueFitID) == target
                        and scalarText(registered.CharacterID) == "EVE" and scalarText(registered.FitMeshType) == "Body"
                        and scalarText(registered.MeshSubType) == "", "control registered metadata mismatch")
                    local specs = type(options.controls) == "table" and options.controls[target]
                    local spec = type(specs) == "table" and specs[key]
                    assert(type(spec) == "table" and finite(value), "control outside bootstrap allowlist")
                    local scalar = spec.kind == "scalar"
                    assert(scalar or spec.kind == "toggle", "unsupported control kind")
                    if scalar then
                        assert(finite(spec.min) and finite(spec.max) and spec.min <= spec.max
                            and value >= spec.min and value <= spec.max, "control outside declared range")
                    else
                        assert(value == 0 or value == 1, "toggle must be zero or one")
                    end
                    local menu = cns.TempMenuUI
                    assert(live(menu) and menu:GetClass():GetFName():ToString() == "WB_CustomNanoSootUI_C"
                        and menu:IsInViewport() and not menu.IsHiddenUI and live(menu.ModActor)
                        and address(menu.ModActor) == address(cns), "current CNS menu required")
                    assert(live(menu.ConfigurablesArea) and menu.ConfigurablesArea:IsVisible()
                        and live(menu.LastClickedOutfitButton)
                        and scalarText(menu.LastClickedOutfitButton.ButtonID) == target,
                        "CNS is configuring a different outfit")
                    local cls = scalar and "WB_SliderButton_C" or "WB_ToggleButton_C"
                    local dataClass = scalar and "BP_CNS_UserConfigData_ScalarControls_C"
                        or "BP_CNS_UserConfigData_MaterialToggle_C"
                    local widget, data
                    for _, candidate in ipairs(FindAllOf(cls) or {}) do
                        if live(candidate) and candidate:GetClass():GetFName():ToString() == cls then
                            local tree = candidate:GetOuter()
                            local owner = live(tree) and tree:GetOuter()
                            if live(owner) and address(owner) == address(menu) then
                                local d = candidate.BaseConfigData
                                if live(d) and d:GetClass():GetFName():ToString() == dataClass
                                    and scalarText(d.DisplayName) == spec.display
                                    and tonumber(d.MaterialIndex) == spec.material then
                                    assert(not widget, "ambiguous control widget")
                                    widget, data = candidate, d
                                end
                            end
                        end
                    end
                    assert(widget and scalarText(data.ControlledBy) == "", "unavailable or dependent control")
                    if scalar then
                        assert(scalarText(data.ParamName) == spec.param and scalarText(data.Association) == "Global"
                            and tonumber(data.LayerIndex) == -1 and math.abs(tonumber(data.Min)-spec.min) < 0.00001
                            and math.abs(tonumber(data.Max)-spec.max) < 0.00001, "control metadata mismatch")
                    end
                    local before = scalarText(data.Value)
                    local preserved = protected(saved, nil) -- every index key, including the real empty key
                    local bodyRemoved = saved.RemovedOutfitMap:Contains("EVE-Body-")
                        and scalarText(saved.RemovedOutfitMap:Find("EVE-Body-"):get()) or "absent"
                    local method = scalar and "OnOutfitConfigurationScalarChanged"
                        or "OnOutfitConfigurationMaterialToggleChanged"
                    reply(id, {op=op, phase="active", control_key=key, native_calls=0, method=method})
                    dispatchedControl = {key=key, method=method}
                    -- Call the original UI consumer once. Never construct/write its configuration TMaps.
                    local wanted = value
                    if not scalar then wanted = value == 1 end
                    menu[method](menu, widget, wanted)
                    local cnsAfter, savedAfter, bodyAfter = wardrobe(c)
                    local removedAfter = savedAfter.RemovedOutfitMap:Contains("EVE-Body-")
                        and scalarText(savedAfter.RemovedOutfitMap:Find("EVE-Body-"):get()) or "absent"
                    local mapsOK = address(cnsAfter) == address(cns) and address(savedAfter) == address(saved)
                        and protected(savedAfter, nil) == preserved and removedAfter == bodyRemoved
                        and bodyAfter.body_id == body.body_id and bodyAfter.body_index == body.body_index
                        and bodyAfter.body_component == body.body_component and bodyAfter.mesh_path == body.mesh_path
                        and bodyAfter.body_visible == body.body_visible
                    local dataOK = scalar and math.abs(tonumber(data.Value)-value) < 0.00001
                        or not scalar and data.Value == wanted
                    state = sample(c)
                    state.op, state.phase, state.native_calls = op, "completed", 1
                    state.control_key, state.method = key, method
                    state.value_before, state.value_after = before, scalarText(data.Value)
                    state.control_data_verified, state.protected_maps_unchanged = dataOK, mapsOK
                    state.material_verification = "independent actual material and visual checks required"
                    reply(id, state); return
                end
                assert(#parts == 11, "select requires explicit outfit and index")
                assert(parts[8] == c.pawn_id and parts[9] == c.world_id, "observed identity required")
                assert(state.blocked == 0 and not state.ignored and state.mode == 1 and state.speed <= 5,
                    "selection requires unlocked stationary walking pawn")
                local menu = cns.TempMenuUI
                assert(not (live(menu) and menu:IsInViewport()), "CNS menu owns selection")
                local id, index = parts[10], tonumber(parts[11])
                local allowed = type(options.outfits) == "table" and options.outfits[id]
                assert(type(allowed) == "table" and finite(index) and index % 1 == 0
                    and index >= 0 and type(allowed[index + 1]) == "string", "outfit/index outside bootstrap allowlist")
                assert(cns.OutfitMap:Contains(id), "outfit not registered in current CNS")
                local data = cns.OutfitMap:Find(id):get()
                assert(live(data) and scalarText(data.UniqueFitID) == id and scalarText(data.CharacterID) == "EVE"
                    and scalarText(data.FitMeshType) == "Body" and scalarText(data.MeshSubType) == "",
                    "registered outfit metadata mismatch")
                local count = tonumber(data.NumFitDatas)
                assert(finite(count) and count >= 0, "invalid outfit data count")
                if count == 0 then -- original CNS schema uses direct paths rather than OutfitDatas
                    count = data.OutfitPaths:GetArrayNum()
                    assert(index < count and scalarText(data.OutfitPaths[index + 1]) == allowed[index + 1],
                        "legacy outfit path/index mismatch")
                end
                assert(index < count, "registered outfit index unavailable")
                local preserved = protected(saved, id)
                reply(parts[2], {op=op, phase="active", pawn=c.pawn_id, world=c.world_id})
                active = {kind="select", id=parts[2], c=c, cns=cns, saved=saved, target=id, index=index,
                    mesh=allowed[index + 1], body_before=body, protected=preserved, calls=1,
                    deadline=deadline, stable=0}
                -- Const reference flags on OutfitID/Index are inputs, not output placeholders.
                -- Only this exact five-parameter function is called, once; never retry uncertain effects.
                cns["Set Custom Outfit"](cns, "EVE", "Body", "", id, index)
                return
            end
            assert(#parts == 9, "unexpected trailing fields")
            if op == "observe" then
                state.op, state.phase = op, "completed"
                reply(id, state); return
            end
            local duration, dx, dy = tonumber(parts[5]), tonumber(parts[6]), tonumber(parts[7])
            assert(finite(duration) and duration % INTERVAL == 0 and duration >= INTERVAL and duration <= 800,
                "duration must be20..800ms in20ms steps")
            assert(finite(dx) and finite(dy) and ((math.abs(dx) == 1 and dy == 0)
                or (dx == 0 and math.abs(dy) == 1)), "world cardinal required")
            assert(parts[8] == c.pawn_id and parts[9] == c.world_id, "observed identity required")
            assert(state.blocked == 0 and not state.ignored and state.mode == 1, "native movement locked or not walking")
            assert(not cnsOwnsInput(c), "CNS menu owns movement")
            local t = clock(c)
            -- Record acceptance before authorizing the first input submission.
            reply(id, {op=op, phase="active", pawn=c.pawn_id, world=c.world_id})
            active = {kind="move", id=id, c=c, before=state, dx=dx, dy=dy, duration=duration,
                previous=t,
                deadline=math.min(deadline, now+2), submissions=0, peak=state.speed}
        end)
        if not ok then
            if dispatchedControl then
                reply(id, {op=op, phase="completed", native_calls=1, control_key=dispatchedControl.key,
                    method=dispatchedControl.method, control_data_verified=false,
                    reason="uncertain native control call: " .. tostring(reason)})
            elseif active and active.id == id then finish("uncertain native call: " .. tostring(reason))
            else reply(id, {op=op, phase="rejected", reason=reason}) end
        end
    end
    -- No old queued action is adopted on startup, including a same-nonce stale file.
    os.remove(base .. "request.txt")
    reply("ready", {phase="ready", lease_seconds=LEASE, started_unix=born})
    handle = LoopInGameThreadWithDelay(INTERVAL, function()
        if closed then return end
        local ok, reason = pcall(function()
            local now = os.time()
            if now < born or now >= born + LEASE then shutdown("session lease expired"); return end
            if active then
                local a = active
                if now > a.deadline then finish("wall deadline expired"); return end
                local state = sample(a.c)
                if a.kind == "select" then
                    local cns, saved, body = wardrobe(a.c)
                    assert(address(cns) == address(a.cns) and address(saved) == address(a.saved), "CNS owner changed")
                    for k, v in pairs(body) do state[k] = v end
                    state.protected_maps_unchanged = protected(saved, a.target) == a.protected
                    assert(state.protected_maps_unchanged, "unrelated saved selection changed")
                    local matches = body.body_id == a.target and body.body_index == a.index
                        and body.mesh_path == a.mesh and body.body_visible
                    a.stable = matches and a.stable + 1 or 0
                    if a.stable >= 2 then finish("selection matched saved data and live mesh", state) end
                else
                    if cnsOwnsInput(a.c) then finish("CNS menu owns movement", state); return end
                    local t = clock(a.c)
                    assert(t >= a.previous, "real clock reset")
                    a.previous, a.peak = t, math.max(a.peak, state.speed)
                    if state.blocked ~= 0 or state.ignored or state.mode ~= 1 then
                        finish("native movement locked or mode changed", state); return
                    end
                    -- Acknowledgement I/O and a delayed first callback must not
                    -- consume the input window before any input is submitted.
                    -- The original wall deadline and session lease still bound it.
                    if a.start == nil then
                        a.start = t
                        a.until_time = t + a.duration / 1000
                        a.settle_until = a.until_time + 0.25
                    end
                    if t >= a.settle_until then finish("duration completed", state); return end
                    if t < a.until_time and a.submissions < a.duration / INTERVAL then
                        a.submissions = a.submissions + 1 -- never retry an uncertain native call
                        a.c.pawn:AddMovementInput({X=a.dx, Y=a.dy, Z=0}, 1.0, false)
                    end
                end
            end
            -- Polling transport and all UObject calls remain on the game thread.
            pollTicks = pollTicks + 1
            if pollTicks >= 5 then
                pollTicks = 0
                readRequest()
            end
        end)
        if not ok then
            if active then pcall(finish, "refused: " .. tostring(reason)) end
            -- Fail closed after an unexpected tick error instead of silently retrying effects.
            shutdown("bridge error")
        end
    end)
    return {stop=function() shutdown("bootstrap stop") end}
end
return M
