-- Session-only test transport. It never executes request text as Lua.
-- Bootstrap owns directory/nonce. Requests are one atomic request.txt, nine TSV fields:
-- nonce,id,expires_unix,op,duration_ms,world_dx,world_dy,pawn,world.
-- select appends outfit_id,index; wardrobe reads the current EVE Body state.
-- move requires addresses returned by observe; direction is one world cardinal.
-- Replies are <id>.response.tsv, one key/value per line, atomically replaced.
local M = {}
local VERSION, INTERVAL, LEASE = "3", 20, 1200
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
        if state then
            out.horizontal_displacement = math.sqrt((state.x-a.before.x)^2 + (state.y-a.before.y)^2)
            out.settled = state.speed <= 5
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
        if (#parts ~= 9 and #parts ~= 11) or parts[1] ~= nonce or not parts[2]:match("^[a-zA-Z0-9_-]+$")
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
        if op ~= "observe" and op ~= "move" and op ~= "wardrobe" and op ~= "select" then
            reply(id, {op=op, phase="rejected", reason="unknown operation"}); return
        end
        if active then reply(id, {op=op, phase="rejected", reason="move busy"}); return end
        local ok, reason = pcall(function()
            local c = resolve()
            local state = sample(c)
            if op == "wardrobe" or op == "select" then
                local cns, saved, body = wardrobe(c)
                for k, v in pairs(body) do state[k] = v end
                if op == "wardrobe" then
                    state.op, state.phase = op, "completed"
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
            local t = clock(c)
            -- Record acceptance before authorizing the first input submission.
            reply(id, {op=op, phase="active", pawn=c.pawn_id, world=c.world_id})
            active = {kind="move", id=id, c=c, before=state, dx=dx, dy=dy, duration=duration,
                start=t, previous=t, until_time=t+duration/1000, settle_until=t+duration/1000+0.25,
                deadline=math.min(deadline, now+2), submissions=0, peak=state.speed}
        end)
        if not ok then
            if active and active.id == id then finish("uncertain native call: " .. tostring(reason))
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
                    local t = clock(a.c)
                    assert(t >= a.previous, "real clock reset")
                    a.previous, a.peak = t, math.max(a.peak, state.speed)
                    if state.blocked ~= 0 or state.ignored or state.mode ~= 1 then
                        finish("native movement locked or mode changed", state); return
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
