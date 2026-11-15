"""Add bounded delayed cleanup to the previously post-init-patched shim.

The caller owns exact preimage validation, game exit, and installation.
"""


def patch(source: str) -> str:
    if 'local DeferredOwned = require("DeferredOwnedRecheck")' in source:
        raise ValueError("deferred integration already present")
    anchor = 'local refreshQueued = false\n'
    helper = '''local refreshQueued = false
local DeferredOwned = require("DeferredOwnedRecheck").new({
    live=live, body=getBody, asset=getMeshAsset,
    delay=ExecuteWithDelay, onGameThread=ExecuteInGameThread,
    busy=function() return refreshing end,
    report=function(err) print("[EveProportionsLifecycle] deferred recheck stopped: " .. tostring(err) .. "\\n") end,
    recheck=function(body, asset)
        refreshing=true
        local ok, result=pcall(function()
            local active=body:GetPostProcessInstance()
            local rebuilt, reason=LinkedOwnership:beforeRefresh(body, active, rememberOriginal(asset))
            if rebuilt then
                active=body:GetPostProcessInstance()
                linkOriginal(active, asset, "deferred owned cleanup", body)
                if Tuner then pcall(function() Tuner.ApplyTo(active) end) end
            end
            return reason
        end)
        refreshing=false
        if not ok then error(result) end
        return result
    end
})
'''
    reason_anchor = '    if not lifecycleOK then\n'
    reason_patch = '''    if lifecycleOK then
        local scheduled, err=pcall(function() DeferredOwned:schedule(lifecycleReason, character, body, asset) end)
        if not scheduled then print("[EveProportionsLifecycle] deferred scheduling stopped: " .. tostring(err) .. "\\n") end
    end
    if not lifecycleOK then
'''
    target_anchor = '    if not live(body) or not live(asset) or not stampAsset(asset, source) then return end\n'
    target_patch = target_anchor + '    DeferredOwned:syncTarget(body, asset)\n'
    for part in (anchor, reason_anchor, target_anchor):
        if source.count(part) != 1:
            raise ValueError("expected exactly one compatible deferred integration site")
    return source.replace(anchor, helper).replace(reason_anchor, reason_patch).replace(target_anchor, target_patch)
