"""Hashed, read-only CNS and Lua interface evidence. Never execute third-party code."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import zipfile

from src.catalog.inventory import emit_json, sha256, stamp
from src.policy.static import archive_metadata
from src.policy.resources import virtual_resource_key

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


def call_prefix(tokens, first):
    """Only read arguments before an anonymous function, never its body."""
    args, current, brackets, after_separator = [], [], [], False
    for value, kind, line in tokens[first:first + 4096]:
        if value == 'function' and kind == 'name':
            return args, 'CALLBACK_FUNCTION_PREFIX' if not current and not brackets else 'FUNCTION_EXPRESSION'
        if value == ')' and kind == 'punct' and not brackets:
            if not current and args and after_separator:
                return args, 'INCOMPLETE_ARGUMENT'
            if current: args.append(current)
            return args, 'CLOSED'
        if value == ',' and kind == 'punct' and not brackets:
            args.append(current); current = []; after_separator = True; continue
        if kind == 'punct' and value in {'(', '{', '['}: brackets.append(value)
        elif kind == 'punct' and value in {')', '}', ']'}:
            if not brackets or brackets.pop() != {')': '(', '}': '{', ']': '['}[value]:
                return args, 'UNBALANCED'
        current.append((value, kind, line))
        after_separator = False
    return args, 'INCOMPLETE_OR_LIMITED'


def candidate_key(argument):
    """Documented spelling/numeric candidate, never actual Lua table binding."""
    values = [t[0] for t in argument]
    if len(argument) == 1 and argument[0][1] == 'number':
        value = values[0]
        if not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', value): return None
        number = int(value, 16 if value.lower().startswith('0x') else 10)
        return number if 0 <= number <= 255 else None
    if len(argument) != 3 or values[:2] != ['Key', '.'] or [t[1] for t in argument] != ['name', 'punct', 'name']:
        return None
    name = values[2]
    if len(name) == 1 and ('A' <= name <= 'Z' or '0' <= name <= '9'): return ord(name)
    if re.fullmatch(r'F(?:[1-9]|1[0-9]|2[0-4])', name): return 111 + int(name[1:])
    digits = ['ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE']
    if name in digits: return 48 + digits.index(name)
    if name.startswith('NUM_') and name[4:] in digits: return 96 + digits.index(name[4:])
    return {'INS': 45, 'DEL': 46, 'HOME': 36, 'END': 35, 'SPACE': 32, 'RETURN': 13,
            'ESCAPE': 27, 'TAB': 9, 'UP_ARROW': 38, 'DOWN_ARROW': 40,
            'LEFT_ARROW': 37, 'RIGHT_ARROW': 39}.get(name)


def candidate_modifiers(argument):
    """An entire literal list is required; partial/dynamic lists stay unknown."""
    values = [t[0] for t in argument]
    if any(kind != ('name' if value in {'ModifierKey', 'SHIFT', 'CONTROL', 'ALT'} else 'punct')
           for value, kind, _ in argument): return None
    if values == ['{', '}']: return []
    if len(values) < 5 or values[0] != '{' or values[-1] != '}': return None
    contents, result = values[1:-1], []
    while contents:
        if len(contents) < 3 or contents[:2] != ['ModifierKey', '.'] or contents[2] not in {'SHIFT', 'CONTROL', 'ALT'}:
            return None
        result.append(contents[2]); contents = contents[3:]
        if contents:
            if contents[0] != ',': return None
            contents = contents[1:]
    return sorted(result) if len(result) == len(set(result)) else None


def keybind_semantics(function, scope, args, shape, line):
    role = ('UNKNOWN_WRAPPER' if scope != 'BARE' else
            'REGISTER_SPELLING' if function in {'RegisterKeyBind', 'RegisterKeyBindAsync'} else
            'QUERY_SPELLING' if function == 'IsKeyBindRegistered' else 'UNKNOWN_WRAPPER')
    count = len(args) + (shape == 'CALLBACK_FUNCTION_PREFIX') if shape in {'CLOSED', 'CALLBACK_FUNCTION_PREFIX'} else None
    key = candidate_key(args[0]) if args else None
    modifiers = None
    if role == 'REGISTER_SPELLING' and count == 2: modifiers = []
    elif role == 'REGISTER_SPELLING' and count == 3: modifiers = candidate_modifiers(args[1])
    return {'line': line, 'function': function, 'role': role, 'callee_scope': scope,
            'argument_shape': shape, 'argument_count': count,
            'candidate_virtual_key': key, 'candidate_modifiers': modifiers,
            'chord_status': 'LITERAL_ARGUMENT_CANDIDATE' if role == 'REGISTER_SPELLING' and scope == 'BARE' and
                            key is not None and modifiers is not None else 'UNRESOLVED',
            'binding_and_execution': 'UNKNOWN; scope/shadowing/branches/activation/callback types and dispatch not verified'}


def lua_evidence(text: str) -> dict:
    tokens = lua_tokens(text)
    rebound = {value for i, (value, kind, _) in enumerate(tokens[:-1]) if kind == 'name' and
               (tokens[i + 1][0] == '=' and (i == 0 or tokens[i - 1][0] not in {'.', ':'}) or
                i > 0 and tokens[i - 1][0] == 'function')}
    # Include multi-name local declarations; no evaluator or full scope analysis.
    for i, (value, kind, line) in enumerate(tokens):
        if value != 'local' or kind != 'name': continue
        for name, kind, row in tokens[i + 1:i + 32]:
            if row != line or name not in {',', 'function'} and kind != 'name': break
            if kind == 'name': rebound.add(name)
    registrations, effects, loads, unresolved, declarations = [], [], [], [], []
    key_semantics, file_accesses = [], []
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
            args, shape = call_prefix(tokens, i + 2)
            values = [[t[0] for t in a] for a in args[:4]]
            literal_key = None
            if args and len(args[0]) == 1 and args[0][0][1] == "number":
                number = candidate_key(args[0])
                if number is not None: literal_key = "VK:" + str(number)
            elif (args and len(args[0]) >= 3 and args[0][-3][0] == "Key"
                  and all(t[1] == "name" if j % 2 == 0 else t[0] == "." for j, t in enumerate(args[0]))):
                literal_key = "KEY:" + args[0][-1][0]
            modifiers = sorted({a[j + 2][0] for a in args[1:2] for j in range(len(a) - 2)
                                if a[j][0] == "ModifierKey" and a[j + 1][0] == "."})
            registrations.append({"line": line, "function": value, "arguments": values, "literal_key": literal_key,
                                  "literal_modifiers": modifiers, "status": "DECLARED_CALLSITE; runtime registration NOT_RUN"})
            scope = 'QUALIFIED_MEMBER' if start != i else 'LOCAL_OR_REASSIGNED' if value in rebound else 'BARE'
            key_semantics.append(keybind_semantics(value, scope, args, shape, line))
        callee = [t[0] for t in tokens[start:i + 1]]
        if is_call and callee in [['io', '.', 'open'], ['os', '.', 'remove'], ['os', '.', 'rename']]:
            args, shape = call_prefix(tokens, i + 2)
            mode = args[1][0][0] if callee[0] == 'io' and len(args) == 2 and len(args[1]) == 1 and args[1][0][1] == 'string' else None
            if callee[0] == 'os': intent = 'MUTATION_SPELLING'
            elif shape == 'CLOSED' and len(args) == 1: intent, mode = 'READ_SPELLING', 'r'
            elif mode is not None and re.fullmatch(r'[rwa]\+?b?', mode):
                intent = 'WRITE_SPELLING' if mode[0] in 'wa' or '+' in mode else 'READ_SPELLING'
            else: intent = 'UNKNOWN'
            file_accesses.append({'line': line, 'function': '.'.join(callee[::2]),
                                  'arguments': [[t[0] for t in a] for a in args[:3]],
                                  'argument_shape': shape, 'literal_mode': mode, 'intent': intent,
                                  'write_ownership': 'UNASSIGNED', 'target_resolution': 'UNVERIFIED; working directory/aliases/branches may differ'})
        if value in {"require", "dofile"} and is_call:
            arg = tokens[i + 2] if i + 2 < len(tokens) else ("", "", line)
            loads.append({"line": line, "function": value, "literal_prefix": arg[0] if arg[1] == "string" else None,
                          "resolved": False})
        for domain, names in DOMAINS.items():
            if value not in names and not (domain == "spring_physics" and value.startswith("AnimGraphNode_SpringBone_")):
                continue
            operation = "SETTER_CALLSITE" if value in SETTERS and is_call else "DIRECT_ASSIGNMENT" if next_value == "=" else "REFERENCE_ONLY"
            effects.append({"domain": domain, "line": line, "symbol": value, "operation": operation})
        if value == "pcall" and is_call:
            indirect_args, indirect_shape = call_prefix(tokens, i + 2)
            if indirect_args and any('keybind' in t[0].lower() for t in indirect_args[0]):
                unresolved.append({"line": line, "reason": "Indirect pcall registration/check; arguments not resolved"})
                key_semantics.append(keybind_semantics('pcall', 'INDIRECT', indirect_args[1:], indirect_shape, line))
        if value in {"load", "loadstring"} and is_call:
            unresolved.append({"line": line, "reason": "Dynamically generated Lua; NOT_INSPECTED"})
    return {"keybind_calls": registrations, "effect_evidence": effects, "module_loads": loads,
            "key_configuration_declarations": declarations,
            "keybind_semantics": key_semantics, "file_accesses": file_accesses,
            "unknowns": unresolved + [{"reason": "Computed keys/properties, wrappers, branches and callback execution require manual/runtime verification"}],
            "scope": "SYNTACTIC_EVIDENCE_ONLY; no effect or registration acceptance"}


def unreal_asset_key(value: str) -> str | None:
    if value.startswith("/Game/"):
        stem = "SB/Content/" + value[6:].split(".")[0]
    elif value.startswith("/Engine/"):
        stem = "Engine/Content/" + value[8:].split(".")[0]
    else:
        return None
    return virtual_resource_key(stem + ".uasset")


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


def read_configuration_evidence(path=None) -> dict:
    path = Path(path) if path is not None else Path(__file__).with_name('configuration_evidence.json')
    evidence, strict = json_metadata(path.read_text())
    if not strict or evidence.get('schema_version') != 1 or not isinstance(evidence.get('sources'), list):
        raise ValueError('Invalid pinned configuration evidence')
    sources = {}
    for source in evidence['sources']:
        digest = source['sha256']
        if not re.fullmatch('[0-9a-f]{64}', digest) or digest in sources:
            raise ValueError('Invalid or duplicate configuration source identity')
        calls = source['calls']
        if len({(c['line'], c['function']) for c in calls}) != len(calls):
            raise ValueError('Duplicate configuration call identity')
        sources[digest] = source
    return sources


def configuration_evidence(data, analysis, sources, module):
    """Attach curated declarations only to exact bytes and exact IO callsites."""
    source = sources.get(hashlib.sha256(data).hexdigest())
    if source is None:
        return {'configuration_evidence_status': 'NO_PINNED_SOURCE_FACTS',
                'configuration_source_observations': []}
    actual = {(c['line'], c['function']): c for c in analysis['file_accesses']}
    expected = {(c['line'], c['function']): c for c in source['calls']}
    if len(actual) != len(analysis['file_accesses']) or actual.keys() != expected.keys() or any(
            actual[key]['intent'] != fact['expected_intent'] for key, fact in expected.items()):
        raise ValueError('Pinned configuration call evidence contradicts exact source analysis')
    for key, fact in expected.items():
        if fact.get('runtime_target') != 'UNVERIFIED' or fact.get('write_ownership') != 'UNASSIGNED':
            raise ValueError('Static evidence cannot grant target resolution or ownership')
        target = {k: deepcopy(v) for k, v in fact.items()
                  if k not in {'line', 'function', 'expected_intent'}}
        target['layout_context'] = 'MATCHING_MODULE_CANDIDATE' if module == source['module_candidate'] else 'MODULE_CONTEXT_UNVERIFIED'
        if target.get('target_key') and module != source['module_candidate']:
            target['expected_layout_target'] = target['target_key']
            target['target_key'] = None
        actual[key]['target_evidence'] = target
    return {'configuration_evidence_status': 'DIGEST_PINNED_DECLARATIONS_ONLY',
            'configuration_source_observations': deepcopy(source['observations'])}


def analyze_document(data: bytes, name: str, configuration_sources=None, module=None) -> dict:
    text = data.decode("utf-8-sig")
    if name.lower().endswith(".lua"):
        analysis = lua_evidence(text)
        sources = read_configuration_evidence() if configuration_sources is None else configuration_sources
        return {"type": "lua", **analysis, **configuration_evidence(data, analysis, sources, module)}
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


def script_target_overlaps(catalog: dict, inventory: dict) -> dict:
    """Compare observed module-relative targets, never authorize deployment."""
    sources = {r["path"]: r for r in inventory["sources"]}
    targets = defaultdict(list)
    ascii_lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    for record in catalog["records"]:
        if record["kind"] in EXCLUDED: continue
        for entry in sources[record["canonical_path"]].get("entries", []):
            if not entry.get("regular", True) or not entry.get("safe_path", True): continue
            name = entry["path"].replace("\\", "/")
            match = re.search(r"(?:^|/)([^/]+)/Scripts/(.+)$", name, re.I)
            if not match: continue
            module, tail = match.groups()
            if any(p in {"", ".", ".."} for p in (module + "/" + tail).split("/")):
                raise ValueError("Ambiguous module script target")
            target = ("Mods/" + module + "/Scripts/" + tail).translate(ascii_lower)
            targets[target].append({"package": record["id"], "entry": entry["path"],
                                    "sha256": entry["sha256"], "size": entry["size"]})
    result = {}
    for target, members in sorted(targets.items()):
        if len(members) < 2: continue
        result[target] = {"members": members,
                          "status": "SAME_SOURCE_BYTES" if len({(m["sha256"], m["size"]) for m in members}) == 1 else "DIFFERENT_SOURCE_BYTES",
                          "scope": "NORMALIZED_MODULE_SCRIPT_LAYOUT; actual deployment mapping/consumer version unverified",
                          "runtime_rule": "UNKNOWN; explicit file provider/override plan needed; no crash or compatibility inferred",
                          "can_apply": False}
    return result


def build_interfaces(catalog: dict, inventory: dict, resources: dict) -> dict:
    sources = {r["path"]: r for r in inventory["sources"]}
    configuration_sources = read_configuration_evidence()
    documents, deferred = [], []
    def inspect(data, name, ident, namespace, digest, module=None, package=None):
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Frozen text content changed")
        try:
            analyzed = analyze_document(data, name, configuration_sources, module)
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
            if chunk["path"] and (key := virtual_resource_key(chunk["path"])):
                live_resources[key].append(c["id"])
    cns_ids = defaultdict(list)
    module_providers, entrypoints = defaultdict(set), defaultdict(set)
    key_reuse, domain_overlap = defaultdict(list), defaultdict(list)
    enabled = {r["module"]: r["enabled"] for r in inventory["activation"]}
    for doc in documents:
        analysis = doc["analysis"]
        if doc["module"]:
            ns_module = (doc["namespace"], doc["module"])
            provider = doc["package"] or doc["module"]
            module_providers[ns_module].add(provider)
            path = doc["name"] if doc["namespace"] == "raw" else doc["id"]
            if re.search(r"(?:^|/)Scripts/main\.lua$", path.replace("\\", "/"), re.I):
                entrypoints[ns_module].add(provider)
        if analysis["type"] == "lua":
            for key in analysis["keybind_semantics"]:
                if key['role'] != 'REGISTER_SPELLING' or key['callee_scope'] != 'BARE' or key['candidate_virtual_key'] is None:
                    continue
                ident = 'VK:' + str(key['candidate_virtual_key'])
                key_reuse[(doc["namespace"], ident)].append({"file": doc["id"], "module": doc["module"], "line": key["line"],
                      'package': doc['package'], 'sha256': doc['sha256'],
                      'modifiers': key['candidate_modifiers'], 'chord_status': key['chord_status'],
                      "enabled_in_current_mods_txt": enabled.get(doc["module"]) if doc["namespace"] == "installed" else None})
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
              'keybind_roles': dict(Counter(k['role'] for d in lua for k in d['analysis']['keybind_semantics'])),
              'literal_chord_candidates': sum(k['chord_status'] == 'LITERAL_ARGUMENT_CANDIDATE' for d in lua for k in d['analysis']['keybind_semantics']),
              'file_access_spellings': sum(len(d['analysis']['file_accesses']) for d in lua),
              'configuration_sources_pinned': sum(d['analysis']['configuration_evidence_status'] == 'DIGEST_PINNED_DECLARATIONS_ONLY' for d in lua),
              "effect_domain_references": dict(Counter(e["domain"] for d in lua for e in d["analysis"]["effect_evidence"]))}
    return {"schema_version": 1, "documents": documents, "deferred": deferred, "summary": summary,
            "module_providers": [{"namespace": ns, "module": module, "providers": sorted(providers),
                                  "entrypoint_providers": sorted(entrypoints[(ns, module)]),
                                  "extension_providers": sorted(providers - entrypoints[(ns, module)]),
                                  "rule": "NAMESPACE_FILES_OBSERVED; main.lua entrypoints and extensions are distinct; exact targets/consumer version/runtime ABI require verification"}
                                 for (ns, module), providers in module_providers.items()],
            "raw_script_target_overlaps": script_target_overlaps(catalog, inventory),
            "potential_key_reuse": [{"namespace": ns, "key": key, "members": members,
                                     "rule": "REGISTER_SPELLING_BASE_KEY_CANDIDATES_ONLY; queries excluded; modifier variants/aliases/branches/activation/dispatch unresolved"}
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
