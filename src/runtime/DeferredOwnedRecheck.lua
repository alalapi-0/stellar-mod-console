-- Bounded rechecks for an owned graph deferred while animation is active.
-- UE objects are touched only on the game thread; every attempt is guarded.
local M = {}
function M.new(env)
    local generation, pending, targetBody, targetAsset = 0, nil, nil, nil
    local api = {}
    function api:syncTarget(body, asset)
        local bodyID, assetID = body:GetAddress(), asset:GetAddress()
        if bodyID ~= targetBody or assetID ~= targetAsset then
            generation, pending = generation + 1, nil
            targetBody, targetAsset = bodyID, assetID
        end
    end
    function api:schedule(reason, character, body, asset)
        if reason ~= "active_or_unknown_animation_preserved" then return false end
        if not env.live(character) or not env.live(body) or not env.live(asset) then return false end
        local ticket = generation
        if pending == ticket then return false end
        local bodyID, assetID = body:GetAddress(), asset:GetAddress()
        pending = ticket
        local function finish()
            if pending == ticket then pending = nil end
        end
        local enqueue
        enqueue = function(attempt)
            local scheduled, scheduleError = pcall(env.delay, 3000, function()
                local queued, queueError = pcall(env.onGameThread, function()
                    if ticket ~= generation or pending ~= ticket then return end
                    local function retryOrFinish()
                        if attempt < 3 then
                            local ok, err = enqueue(attempt + 1)
                            if not ok then env.report(err) end
                        else
                            finish()
                        end
                    end
                    if env.busy() then retryOrFinish(); return end
                    local ok, result = pcall(function()
                        if not env.live(character) then return "target_changed" end
                        local current = env.body(character)
                        if not env.live(current) or current:GetAddress() ~= bodyID then return "target_changed" end
                        local currentAsset = env.asset(current)
                        if not env.live(currentAsset) or currentAsset:GetAddress() ~= assetID then return "target_changed" end
                        return env.recheck(current, currentAsset)
                    end)
                    if not ok then finish(); env.report(result)
                    elseif result == "active_or_unknown_animation_preserved" then retryOrFinish()
                    else finish() end
                end)
                if not queued then finish(); env.report(queueError) end
            end)
            if not scheduled then finish(); return false, scheduleError end
            return true
        end
        local ok, err = enqueue(1)
        if not ok then error(err) end
        return true
    end
    return api
end
return M
