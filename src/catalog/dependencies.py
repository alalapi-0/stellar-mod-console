"""R02 metadata discovery, not an enablement or compatibility decision engine."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath
import re

from .inventory import emit_json, sha256, stamp


def filename_hints(name: str) -> dict:
    standard = re.search(r"-(\d{1,5})-(.+)-(\d{10})(?: \(\d+\))?\.(?:zip|rar|7z|exe)$", name, re.I)
    dated = re.search(r" (\d{1,5}) ([^ ]+) 20\d\d-\d\d-\d\dT", name)
    if standard:
        return {"mod_id": int(standard[1]), "version": standard[2], "authority": "FILENAME_HINT_ONLY"}
    if dated:
        return {"mod_id": int(dated[1]), "version": dated[2], "authority": "FILENAME_HINT_ONLY"}
    return {"mod_id": None, "version": None, "authority": "UNKNOWN"}


def components(entries: list[dict]) -> dict:
    paths = [e["path"].replace("\\", "/") for e in entries]
    triplets = defaultdict(dict)
    modules = set()
    for entry, path in zip(entries, paths):
        p = PurePosixPath(path)
        if p.suffix.lower() in {".pak", ".utoc", ".ucas"}:
            triplets[str(p.with_suffix(""))][p.suffix.lower()] = entry["sha256"]
        m = re.search(r"(?:^|/)Mods/([^/]+)/Scripts/", path, re.I)
        if not m:
            m = re.search(r"(?:^|/)([^/]+)/Scripts/main\.lua$", path, re.I)
        if m:
            modules.add(m[1])
    return {"containers": [{"stem": stem, "files": files,
                             "layout": "IOSTORE_COMPLETE" if set(files) == {".pak", ".utoc", ".ucas"} else
                             "PAK_ONLY" if set(files) == {".pak"} else "INCOMPLETE_OR_NONSTANDARD"}
                            for stem, files in sorted(triplets.items())],
            "lua_modules": sorted(modules),
            "dll_files": [p for p in paths if p.lower().endswith(".dll")],
            "cns_configs": [p for p in paths if p.lower().endswith(".dekcns.json")],
            "animation_configs": [p for p in paths if p.lower().endswith(".dekani.json")]}


def content_kind(row: dict, parts: dict) -> str:
    attribution = row["attribution"]
    if attribution in {"incomplete_download", "other_game_historical", "other_game_content", "unrelated_loose_download"}:
        return attribution
    suffixes = {PurePosixPath(e["path"].replace("\\", "/")).suffix.lower() for e in row.get("entries", [])}
    if any(PurePosixPath(p).name.lower() == "ue4ss.dll" for p in parts["dll_files"]):
        return "loader_bundle"
    if Path(row["path"]).suffix.lower() == ".exe" or ".exe" in suffixes:
        return "windows_tool_or_runtime"
    if parts["lua_modules"]:
        return "lua_mod_with_content" if parts["containers"] else "lua_mod"
    if parts["cns_configs"]:
        return "cns_content"
    if parts["containers"]:
        return "container_mod"
    if ".asi" in suffixes:
        return "asi_loader_or_plugin"
    if suffixes & {".uasset", ".umap", ".uproject"}:
        return "unpacked_unreal_resource"
    if suffixes == {".ini"}:
        return "engine_configuration"
    if ".bk2" in suffixes:
        return "movie_replacement"
    if suffixes & {".bmp", ".jpg", ".png"}:
        return "image_or_splash_asset"
    return "unknown_visible"


def discover(inventory: dict, facts: dict) -> dict:
    by_digest = defaultdict(list)
    for row in inventory["sources"]:
        # Partial/error inputs retain independent identities and cannot alias usable bytes.
        key = row["sha256"] if row["integrity"] in {"DECODED", "EXECUTABLE_NOT_RUN", "NOT_APPLICABLE", "EXCLUDED_OTHER_GAME_NOT_DECODED"} else row["id"]
        by_digest[key].append(row)
    nodes = {n["id"]: dict(n) for n in facts["dependency_nodes"]}
    exact_facts = {r["archive_sha256"]: r for r in facts.get("exact_file_facts", [])}
    if len(exact_facts) != len(facts.get("exact_file_facts", [])):
        raise ValueError("Duplicate exact-file fact binding")
    records = []
    for digest, aliases in sorted(by_digest.items()):
        aliases.sort(key=lambda r: ("historical" not in r, r["path"]))
        row = aliases[0]
        parts = components(row.get("entries", []))
        kind = content_kind(row, parts)
        excluded = kind in {"incomplete_download", "other_game_historical", "other_game_content", "unrelated_loose_download"}
        hints = filename_hints(Path(row["path"]).name)
        author = facts["mods"].get(str(hints["mod_id"])) if not excluded else None
        exact = exact_facts.get(row["sha256"]) if not excluded else None
        if exact and exact["readme_entry_sha256"] not in {e["sha256"] for e in row.get("entries", [])}:
            raise ValueError("Exact README evidence does not match frozen archive")
        requirements = []
        if parts["cns_configs"] and not excluded:
            requirements.append({"target": "mod:1496", "evidence": "observed CNS config format", "confidence": "FORMAT_REQUIRED_VERSION_UNKNOWN"})
        if author:
            for requirement in author.get("requirements", []):
                requirements.append(dict(requirement, source=author["url"], confidence="AUTHOR_PAGE_CONTEXT_NOT_EXACT_FILE"))
        if exact:
            for requirement in exact["requirements"]:
                requirements.append(dict(requirement, confidence="EXACT_HASHED_README_RUNTIME_NOT_RUN"))
        # Unknown dependency discovery is a node, never an empty list meaning 'no requirements'.
        if not excluded:
            unknown = "requirements:unknown:" + row["id"]
            nodes[unknown] = {"id": unknown, "status": "UNKNOWN", "reason": "Body/texture/DLC/ABI and exact file requirements need per-version verification"}
            requirements.append({"target": unknown, "confidence": "UNKNOWN"})
        record = {"id": row["id"], "content_sha256": row["sha256"], "canonical_path": row["path"],
                  "aliases": [{"id": r["id"], "path": r["path"], "historically_referenced": "historical" in r} for r in aliases],
                  "kind": kind, "integrity": row["integrity"], "filename_hints": hints,
                  "components": parts, "requirements": requirements,
                  "exact_file_evidence": exact,
                  "permission": {"status": "EXCLUDED_NO_PROJECT_COPY" if excluded else
                                 "PAGE_RESTRICTION_RECORDED_EXACT_FILE_NOT_GRANTED" if author and author["page_permissions"].get("upload") == "forbidden" else "PERMISSION_UNKNOWN",
                                 "source": author["url"] if author else None,
                                 "redistribute": False, "copy_into_project": False},
                  "application": "EXIT_GAME_REQUIRED_OR_NOT_SUPPORTED",
                  "runtime_compatibility": "NOT_RUN", "performance": "UNKNOWN", "difficulty_effect": "UNKNOWN"}
        records.append(record)
    ids = set(nodes)
    dangling = [r for r in records for r in r["requirements"] if r["target"] not in ids]
    if dangling:
        raise ValueError("Unresolved dependency graph references")
    all_inputs = [a["id"] for r in records for a in r["aliases"]]
    expected = [r["id"] for r in inventory["sources"]]
    if Counter(all_inputs) != Counter(expected) or len(all_inputs) != len(set(all_inputs)):
        raise ValueError("Input ownership is not exhaustive/disjoint")
    relevant = [r for r in records if r["kind"] not in {"incomplete_download", "other_game_historical", "other_game_content", "unrelated_loose_download"}]
    summary = {"input_references": len(all_inputs), "canonical_records": len(records),
               "content_aliases": len(all_inputs) - len(records), "relevant_candidate_records": len(relevant),
               "kinds": dict(Counter(r["kind"] for r in records)),
               "unknown_exact_file_requirements": len(relevant),
               "records_with_exact_readme_binding": sum(r["exact_file_evidence"] is not None for r in relevant),
               "permission_unknown": sum(r["permission"]["status"] == "PERMISSION_UNKNOWN" for r in relevant),
               "page_restrictions_recorded": sum(r["permission"]["status"] != "PERMISSION_UNKNOWN" for r in relevant),
               "redistribution_grants": 0, "dependency_nodes": len(nodes), "dangling_references": len(dangling),
               "archive_container_groups": sum(len(r["components"]["containers"]) for r in relevant),
               "incomplete_or_nonstandard_containers": sum(c["layout"] == "INCOMPLETE_OR_NONSTANDARD" for r in relevant for c in r["components"]["containers"]),
               "runtime_compatibility": "NOT_RUN", "cleanup": "NOT_APPLIED; all original paths retained"}
    return {"schema_version": 1, "summary": summary, "records": records, "dependency_nodes": list(nodes.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private_root = Path(__file__).resolve().parents[2] / ".local"
    if private_root not in args.output.resolve().parents:
        raise ValueError("Raw discovery output must stay in this repository's ignored .local directory")
    inventory = json.loads(args.inventory.read_text())
    # This pass reuses the frozen hashes only while the source stamps remain valid.
    for row in inventory["sources"]:
        if stamp(Path(row["path"])) != tuple(row["stamp"]):
            raise ValueError("Frozen source changed; re-inventory the exact input before discovery")
    result = discover(inventory, json.loads(args.facts.read_text()))
    result["inventory_sha256"] = sha256(args.inventory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emit_json(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False))
