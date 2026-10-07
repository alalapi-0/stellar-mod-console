"""Hashed, read-only CNS and Lua interface evidence. Never execute third-party code."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import zipfile

from src.catalog.inventory import emit_json, sha256, stamp
from src.policy.static import archive_metadata

EXCLUDED = {"other_game_historical", "other_game_content", "incomplete_download", "unrelated_loose_download"}
MAX_TEXT = 4 * 1024**2
DOMAINS = {
    "world_time": {"SetGlobalTimeDilation", "GlobalTimeDilation", "TimeDilation"},
    "actor_time": {"CustomTimeDilation"},
    "movement": {"MaxWalkSpeed", "MaxFlySpeed", "GravityScale", "SetMovementMode", "LaunchCharacter", "JumpZVelocity"},
    "spring_physics": {"SpringBone", "AnimGraphNode_SpringBone", "SpringBoneTweaks"},
    "appearance": {"SetSkeletalMesh", "SetMaterial", "SetAnimInstanceClass", "SetPhysicsAsset"},
    "camera": {"SetFieldOfView", "SetCameraLocation", "FieldOfView", "FOV", "CameraBoom", "TargetArmLength"},
}
SETTERS = {"SetGlobalTimeDilation", "SetMovementMode", "LaunchCharacter", "SetSkeletalMesh", "SetMaterial",
           "SetAnimInstanceClass", "SetPhysicsAsset", "SetFieldOfView", "SetCameraLocation"}


def lua_tokens(text: str) -> list[tuple[str, str, int]]:
    """Small lexer for evidence, not a Lua interpreter/parser. Comments never count."""
    tokens, i, line = [], 0, 1
    atom = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?")
    bracket = re.compile(r"\[(=*)\[")
    while i < len(text):
        start, start_line = i, line
        if text[i].isspace():
            line += text[i] == "\n"
            i += 1
            continue
        comment = text.startswith("--", i)
        block = bracket.match(text, i + 2 if comment else i)
        if block:
            close = "]" + block[1] + "]"
            end = text.find(close, block.end())
            if end < 0:
                raise ValueError("Unterminated Lua long string/comment")
            i = end + len(close)
            if not comment:
                tokens.append((text[block.end():end], "string", line))
        elif comment:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text[i] in "\"'":
            quote, value = text[i], []
            i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                    if i >= len(text):
                        raise ValueError("Unterminated Lua escape")
                    # Preserve arbitrary escapes as uncertain literal values.
                    value.append("\\" + text[i])
                else:
                    value.append(text[i])
                i += 1
            if i >= len(text):
                raise ValueError("Unterminated Lua string")
            i += 1
            tokens.append(("".join(value), "string", line))
        elif match := atom.match(text, i):
            value = match[0]
            tokens.append((value, "name" if value[0].isalpha() or value[0] == "_" else "number", line))
            i = match.end()
        else:
            value = next((op for op in ("...", "..", "==", "~=", "<=", ">=") if text.startswith(op, i)), text[i])
            tokens.append((value, "punct", line))
            i += len(value)
        line += text.count("\n", start, i)
    return tokens


def lua_evidence(text: str) -> dict:
    tokens = lua_tokens(text)
    registrations, effects, loads, unresolved, declarations = [], [], [], [], []
    for i, (value, kind, line) in enumerate(tokens):
        if kind == "string":
            for domain, names in DOMAINS.items():
                if value in names or (domain == "spring_physics" and value.startswith("AnimGraphNode_SpringBone")):
                    effects.append({"domain": domain, "line": line, "symbol": value, "operation": "REFERENCE_IN_STRING"})
        if kind != "name":
            continue
        next_value = tokens[i + 1][0] if i + 1 < len(tokens) else None
        if next_value == "=" and any(word in value.lower() for word in ("key", "modifier", "hotkey")):
            expression = []
            depth = 0
            for token in tokens[i + 2:i + 32]:
                if depth == 0 and (token[0] in {",", "}", ";"} or token[2] > line): break
                if token[0] == "{": depth += 1
                if token[0] == "}": depth -= 1
                expression.append(token[0])
            declarations.append({"line": line, "field": value, "expression": expression,
                                 "binding": "DECLARED_VALUE_ONLY; runtime reader/default precedence unresolved"})
        # A function declaration is not a registration/callsite.
        start = i
        while start >= 2 and tokens[start - 1][0] in {".", ":"}:
            start -= 2
        declaration = start > 0 and tokens[start - 1][0] == "function"
        is_call = next_value == "(" and not declaration
        if "keybind" in value.lower() and is_call:
            # Inspect arguments up to callback/function; dynamic expressions stay unknown.
            args, current, depth = [], [], 0
            for token in tokens[i + 2:]:
                v = token[0]
                if v == "function":
                    break
                if v == ")" and depth == 0:
                    if current:
                        args.append(current)
                    break
                if v == "," and depth == 0:
                    args.append(current)
                    current = []
                    continue
                if v in {"(", "{", "["}: depth += 1
                if v in {")", "}", "]"}: depth -= 1
                current.append(token)
                if len(current) > 100: break
            values = [[t[0] for t in a] for a in args[:4]]
            literal_key = None
            if args and len(args[0]) == 1 and args[0][0][1] == "number":
                literal_key = "VK:" + str(int(args[0][0][0], 0))
            elif (args and len(args[0]) >= 3 and args[0][-3][0] == "Key"
                  and all(t[1] == "name" if j % 2 == 0 else t[0] == "." for j, t in enumerate(args[0]))):
                literal_key = "KEY:" + args[0][-1][0]
            modifiers = sorted({a[j + 2][0] for a in args[1:2] for j in range(len(a) - 2)
                                if a[j][0] == "ModifierKey" and a[j + 1][0] == "."})
            registrations.append({"line": line, "function": value, "arguments": values, "literal_key": literal_key,
                                  "literal_modifiers": modifiers, "status": "DECLARED_CALLSITE; runtime registration NOT_RUN"})
        if value in {"require", "dofile"} and is_call:
            arg = tokens[i + 2] if i + 2 < len(tokens) else ("", "", line)
            loads.append({"line": line, "function": value, "literal_prefix": arg[0] if arg[1] == "string" else None,
                          "resolved": False})
        for domain, names in DOMAINS.items():
            if value not in names and not (domain == "spring_physics" and value.startswith("AnimGraphNode_SpringBone_")):
                continue
            operation = "SETTER_CALLSITE" if value in SETTERS and is_call else "DIRECT_ASSIGNMENT" if next_value == "=" else "REFERENCE_ONLY"
            effects.append({"domain": domain, "line": line, "symbol": value, "operation": operation})
        if value == "pcall" and is_call and i + 2 < len(tokens) and "KeyBind" in tokens[i + 2][0]:
            unresolved.append({"line": line, "reason": "Indirect pcall registration/check; arguments not resolved"})
        if value in {"load", "loadstring"} and is_call:
            unresolved.append({"line": line, "reason": "Dynamically generated Lua; NOT_INSPECTED"})
    return {"keybind_calls": registrations, "effect_evidence": effects, "module_loads": loads,
            "key_configuration_declarations": declarations,
            "unknowns": unresolved + [{"reason": "Computed keys/properties, wrappers, branches and callback execution require manual/runtime verification"}],
            "scope": "SYNTACTIC_EVIDENCE_ONLY; no effect or registration acceptance"}


def unreal_asset_key(value: str) -> str | None:
    if value.startswith("/Game/"):
        stem = "SB/Content/" + value[6:].split(".")[0]
    elif value.startswith("/Engine/"):
        stem = "Engine/Content/" + value[8:].split(".")[0]
    else:
        return None
    return (stem + ".uasset").lower()


def cns_evidence(data: object) -> dict:
    rows = data if isinstance(data, list) else [data]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Unsupported CNS row topology")
    records = []
    for index, row in enumerate(rows):
        if row.get("UniqueFitID") is not None and not isinstance(row["UniqueFitID"], str):
            raise ValueError("Invalid CNS identity type")
        for field in ("OutfitPaths", "OutfitDatas"):
            if field in row and not isinstance(row[field], list):
                raise ValueError("Invalid CNS variant array")
        refs = []
        def visit(value, field):
            if isinstance(value, dict):
                for k, v in value.items(): visit(v, field + "/" + k)
            elif isinstance(value, list):
                for i, v in enumerate(value): visit(v, field + "/" + str(i))
            elif isinstance(value, str) and (key := unreal_asset_key(value)):
                refs.append({"field": field, "asset": value, "resource_key": key})
        # Exclude display/prose; this is a declared dependency graph.
        for field in ("OutfitPaths", "OutfitDatas", "OutfitImage", "PonyPhysics", "AnimationBP", "UserConfigs"):
            visit(row.get(field), field)
        records.append({"row": index, "unique_fit_id": row.get("UniqueFitID"), "character": row.get("CharacterID"),
                        "requirement": row.get("Requirement"), "fit_mesh_type": row.get("FitMeshType"),
                        "declared_path_slots": len(row.get("OutfitPaths", [])),
                        "declared_data_slots": len(row.get("OutfitDatas", [])), "references": refs,
                        "runtime_validation": "NOT_RUN"})
    ids = Counter(r["unique_fit_id"] for r in records if isinstance(r["unique_fit_id"], str))
    return {"records": records, "within_file_duplicate_ids": {k: v for k, v in ids.items() if v > 1},
            "missing_ids": sum(not r["unique_fit_id"] for r in records),
            "namespace": "(ConfigPath, UniqueFitID); live DekCNS Lua lookup uses both"}


def wanted(name: str) -> bool:
    n = name.lower()
    return n.endswith((".lua", ".dekcns.json", ".dekani.json", ".ini", ".cfg"))


def json_metadata(text: str) -> tuple[object, bool]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError("Duplicate JSON object key: " + key)
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=unique_pairs), True
    except json.JSONDecodeError:
        # Recover metadata only. Never write repaired author configuration or call it compatible.
        result, quoted, escaped = [], False, False
        for i, c in enumerate(text):
            if not quoted and c == "," and re.match(r"\s*[}\]]", text[i + 1:]): continue
            result.append(c)
            if c == '"' and not escaped: quoted = not quoted
            escaped = c == "\\" and not escaped if quoted else False
        return json.loads("".join(result), object_pairs_hook=unique_pairs), False


def analyze_document(data: bytes, name: str) -> dict:
    text = data.decode("utf-8-sig")
    if name.lower().endswith(".lua"):
        return {"type": "lua", **lua_evidence(text)}
    if name.lower().endswith(".dekcns.json"):
        obj, valid = json_metadata(text)
        return {"type": "cns", **cns_evidence(obj), "strict_json_valid": valid,
                "format_status": "STRICT_JSON_VALID" if valid else "TRAILING_COMMAS_REMOVED_FOR_METADATA_ONLY; original format NOT_ACCEPTED"}
    if name.lower().endswith(".dekani.json"):
        return {"type": "animation_json", "topology": type(json.loads(text)).__name__, "interface": "UNKNOWN; animation ownership not inferred from name"}
    # Preserve a bounded parameter index, not tool-specific semantic claims.
    pairs = [{"line": i, "key": m[1].strip(), "value": m[2].strip()} for i, line in enumerate(text.splitlines(), 1)
             if (m := re.match(r"^\s*([^;#\[=]+?)\s*=\s*(.*?)\s*$", line))]
    return {"type": "configuration", "parameters": pairs, "effect": "UNKNOWN; reader/binding NOT_VERIFIED"}


def build_interfaces(catalog: dict, inventory: dict, resources: dict) -> dict:
    sources = {r["path"]: r for r in inventory["sources"]}
    documents, deferred = [], []
    def inspect(data, name, ident, namespace, digest, module=None, package=None):
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Frozen text content changed")
        try:
            analyzed = analyze_document(data, name)
            documents.append({"id": ident, "name": name, "namespace": namespace, "sha256": digest,
                              "module": module, "package": package, "analysis": analyzed})
        except (ValueError, TypeError) as error:
            deferred.append({"id": ident, "namespace": namespace, "reason": str(error), "sha256": digest})
    for record in catalog["records"]:
        if record["kind"] in EXCLUDED: continue
        row = sources[record["canonical_path"]]
        entries = [e for e in row.get("entries", []) if wanted(e["path"])]
        if not entries: continue
        path = Path(row["path"])
        selected = {e["path"].replace("\\", "/") for e in entries if e["size"] <= MAX_TEXT}
        members = archive_metadata(path, selected, MAX_TEXT) if path.suffix.lower() != ".zip" and selected else {}
        for e in entries:
            name = e["path"].replace("\\", "/")
            ident = record["id"] + "::" + name
            if e["size"] > MAX_TEXT:
                deferred.append({"id": ident, "namespace": "raw", "reason": "TEXT_LIMIT; NOT_INSPECTED"});continue
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as z: data = z.read(e["path"])
            else: data = members[name]
            match = re.search(r"(?:^|/)([^/]+)/Scripts/", name, re.I)
            inspect(data, name, ident, "raw", e["sha256"], match[1] if match else None, record["id"])
    for row in inventory["installed"]:
        if not wanted(row["path"]): continue
        path = Path(row["path"])
        if row["size"] > MAX_TEXT:
            deferred.append({"id": str(path), "namespace": "installed", "reason": "TEXT_LIMIT; NOT_INSPECTED"});continue
        match = re.search(r"/Mods/([^/]+)/", str(path))
        inspect(path.read_bytes(), path.name, str(path), "installed", row["sha256"], match[1] if match else None)
    live_resources = defaultdict(list)
    for c in resources["installed"]:
        for chunk in c["metadata"]["chunks"]:
            if chunk["resource_key"]: live_resources[chunk["resource_key"]].append(c["id"])
    cns_ids = defaultdict(list)
    module_providers, key_reuse, domain_overlap = defaultdict(set), defaultdict(list), defaultdict(list)
    enabled = {r["module"]: r["enabled"] for r in inventory["activation"]}
    for doc in documents:
        analysis = doc["analysis"]
        if doc["module"]:
            module_providers[(doc["namespace"], doc["module"])].add(doc["package"] or doc["module"])
        if analysis["type"] == "lua":
            for key in analysis["keybind_calls"]:
                if not key["literal_key"]: continue
                ident = key["literal_key"]
                if ident.startswith("KEY:"):
                    name = ident[4:]
                    if re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])", name): ident = "VK:" + str(111 + int(name[1:]))
                    elif len(name) == 1 and ("A" <= name <= "Z" or "0" <= name <= "9"): ident = "VK:" + str(ord(name))
                key_reuse[(doc["namespace"], ident)].append({"file": doc["id"], "module": doc["module"], "line": key["line"],
                      "modifiers": key["literal_modifiers"], "enabled_in_current_mods_txt": enabled.get(doc["module"]) if doc["namespace"] == "installed" else None})
            for effect in analysis["effect_evidence"]:
                domain_overlap[(doc["namespace"], effect["domain"])].append({"file": doc["id"], "module": doc["module"], **effect,
                     "enabled_in_current_mods_txt": enabled.get(doc["module"]) if doc["namespace"] == "installed" else None})
        if analysis["type"] != "cns": continue
        for row in analysis["records"]:
            if row["unique_fit_id"]: cns_ids[(doc["namespace"], row["unique_fit_id"])].append({"file": doc["id"], "row": row["row"]})
            for ref in row["references"]:
                present = ref["resource_key"] in live_resources
                ref["current_index_presence"] = "PRESENT_METADATA_ONLY" if present else "NOT_FOUND_IN_IOSTORE_DIRECTORY_INDEX"
                ref["compatibility"] = "UNKNOWN; path presence does not prove class/skeleton/physics compatibility"
    summary = {"documents": len(documents), "deferrals": len(deferred)}
    for namespace in ("raw", "installed"):
        subset = [d for d in documents if d["namespace"] == namespace]
        summary[namespace + "_types"] = dict(Counter(d["analysis"]["type"] for d in subset))
        cns = [d for d in subset if d["analysis"]["type"] == "cns"]
        summary[namespace + "_cns"] = {"files": len(cns), "rows": sum(len(d["analysis"]["records"]) for d in cns),
              "within_file_duplicate_ids": sum(len(d["analysis"]["within_file_duplicate_ids"]) for d in cns),
              "missing_ids": sum(d["analysis"]["missing_ids"] for d in cns),
              "strict_format_failures_metadata_recovered": sum(not d["analysis"]["strict_json_valid"] for d in cns)}
        lua = [d for d in subset if d["analysis"]["type"] == "lua"]
        summary[namespace + "_lua"] = {"files": len(lua), "keybind_calls": sum(len(d["analysis"]["keybind_calls"]) for d in lua),
              "literal_key_calls": sum(k["literal_key"] is not None for d in lua for k in d["analysis"]["keybind_calls"]),
              "effect_domain_references": dict(Counter(e["domain"] for d in lua for e in d["analysis"]["effect_evidence"]))}
    return {"schema_version": 1, "documents": documents, "deferred": deferred, "summary": summary,
            "module_providers": [{"namespace": ns, "module": module, "providers": sorted(providers),
                                  "rule": "ONE_MODULE_PROVIDER_REQUIRED; exact-file deployment and runtime ABI remain unverified"}
                                 for (ns, module), providers in module_providers.items()],
            "potential_key_reuse": [{"namespace": ns, "key": key, "members": members,
                                     "rule": "UNKNOWN; modifiers/branches/current enablement and indirect keys need resolution"}
                                    for (ns, key), members in key_reuse.items() if len({m["module"] for m in members}) > 1],
            "domain_overlap_evidence": [{"namespace": ns, "domain": domain, "members": members,
                                         "rule": "UNKNOWN; references are not write ownership or proof of simultaneous execution"}
                                        for (ns, domain), members in domain_overlap.items() if len({m["module"] for m in members}) > 1],
            "cross_file_id_reuse": [{"namespace": ns, "id": ident, "members": members,
                                      "rule": "UNKNOWN; reuse across ConfigPath is not alone a collision"}
                                     for (ns, ident), members in cns_ids.items() if len(members) > 1],
            "limits": ["Only syntactic Lua evidence; callbacks, indirect/configured binds and dynamic property writes remain unverified",
                       "Asset references matched current directory metadata only; missing indexed paths may exist in PAK/native/generated resources",
                       "No all-mod compatibility or per-body/physics fit accepted; no third-party code executed or copied into public source"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "inventory", "resources", "output"): p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    if Path(__file__).resolve().parents[2] / ".local" not in args.output.resolve().parents:
        raise ValueError("Private interface evidence must remain in ignored .local")
    inv = json.loads(args.inventory.read_text())
    rows = inv["sources"] + inv["installed"]
    def unchanged():
        if any(stamp(Path(r["path"])) != tuple(r["stamp"]) for r in rows):
            raise ValueError("Frozen input changed; refresh affected input")
    unchanged()
    resources = json.loads(args.resources.read_text())
    if resources["inventory_sha256"] != sha256(args.inventory) or resources["catalog_sha256"] != sha256(args.catalog):
        raise ValueError("Stale resource input")
    result = build_interfaces(json.loads(args.catalog.read_text()), inv, resources)
    unchanged()
    result["input_sha256"] = {name: sha256(getattr(args, name)) for name in ("catalog", "inventory", "resources")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emit_json(args.output, result)
    print(json.dumps(result["summary"]))
