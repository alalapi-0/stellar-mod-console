"""Prepare the small owned integration change against the known shim.

The caller is responsible for checking the installed file's exact preimage,
game/process ownership and atomic deployment. This function performs no I/O.
"""


def patch(source: str) -> str:
    before_policy = (
        "    local lifecycleOK, rebuilt = pcall(function() return "
        "LinkedOwnership:beforeRefresh(body, active, rememberOriginal(asset)) end)\n"
    )
    after_policy = before_policy.replace(
        "local lifecycleOK, rebuilt =", "local lifecycleOK, rebuilt, lifecycleReason ="
    )
    before_link = (
        "    local after = nil\n"
        "    pcall(function() after = body:GetPostProcessInstance() end)\n"
        "    linkOriginal(after, asset, source, body)\n"
    )
    after_link = (
        "    local after = nil\n"
        "    pcall(function() after = body:GetPostProcessInstance() end)\n"
        "    if initialized and live(after) and lifecycleReason == \"postprocess_identity_changed\" then\n"
        "        refreshing = true\n"
        "        local retryOK, retryRebuilt = pcall(function() return "
        "LinkedOwnership:afterInitialize(body, after, rememberOriginal(asset), lifecycleReason) end)\n"
        "        refreshing = false\n"
        "        if not retryOK then\n"
        "            print(\"[EveProportionsLifecycle] post-init recheck stopped: \" .. tostring(retryRebuilt) .. \"\\n\")\n"
        "            return\n"
        "        end\n"
        "        if retryRebuilt then pcall(function() after = body:GetPostProcessInstance() end) end\n"
        "    end\n"
        "    linkOriginal(after, asset, source, body)\n"
    )
    if source.count(before_policy) != 1 or source.count(before_link) != 1:
        raise ValueError("expected exactly one compatible policy and mode-initialization site")
    return source.replace(before_policy, after_policy).replace(before_link, after_link)
