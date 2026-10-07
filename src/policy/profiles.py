"""Read-only configuration previews from discovery evidence, never deployment.

Package references select the whole raw bundle. Component references select only
that container or loader, leaving bundled scripts unselected. The distinction is
deliberate: neither a category nor a source package implies compatibility.
This experimental preview does not freeze the eventual runtime adapter contract.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat

from src.policy.static import check_known_selection

EXCLUDED = {"incomplete_download", "other_game_historical", "other_game_content",
            "unrelated_loose_download"}


def unique(rows, label):
    result = {row["id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"Duplicate {label} identity")
    return result


class ProfilePlanner:
    def __init__(self, catalog, graph, interfaces):
        self.records = unique(catalog["records"], "package")
        self.components = unique(graph["variants"], "component")
        self.nodes = unique(catalog["dependency_nodes"], "requirement")
        self.graph, self.interfaces = graph, interfaces
        if self.records.keys() & self.components.keys():
            raise ValueError("Ambiguous package/component identity")
        self.by_package = {key: set() for key in self.records}
        for key, component in self.components.items():
            if component["package"] not in self.records:
                raise ValueError("Component has no source package")
            self.by_package[component["package"]].add(key)
        for record in self.records.values():
            if any(r["target"] not in self.nodes for r in record["requirements"]):
                raise ValueError("Dangling declared requirement")
        for group in graph["known_exclusive_groups"]:
            if (len(group["members"]) != len(set(group["members"])) or
                    not set(group["members"]) <= self.components.keys() or
                    type(group["max_enabled"]) is not int or group["max_enabled"] < 0):
                raise ValueError("Invalid exclusion group")

    def package(self, ref):
        return ref if ref in self.records else self.components[ref]["package"]

    def provider_candidates(self, target):
        """Candidate discovery only: filename hints never verify ABI/version."""
        if target.startswith("loader:"):
            wanted = self.nodes[target].get("sha256")
            return sorted(key for key, c in self.components.items()
                          if c.get("component") == "UE4SS loader" and wanted and
                          self.records[c["package"]]["content_sha256"] == wanted)
        if target.startswith("mod:"):
            return sorted(key for key, r in self.records.items()
                          if r["kind"] not in EXCLUDED and
                          str(r["filename_hints"].get("mod_id")) == target[4:])
        return []

    def inspect(self, profile):
        if (not isinstance(profile, dict) or
                set(profile) != {"schema_version", "name", "selections", "providers"} or
                type(profile["schema_version"]) is not int or profile["schema_version"] != 1 or
                not isinstance(profile["name"], str) or not profile["name"].strip() or
                not isinstance(profile["selections"], list) or
                any(not isinstance(x, str) for x in profile["selections"]) or
                not isinstance(profile["providers"], dict) or
                any(not isinstance(k, str) or not isinstance(v, str)
                    for k, v in profile["providers"].items())):
            raise ValueError("Invalid preview profile; no application performed")
        refs = set(profile["selections"])
        violations, blockers, visited, whole, components, packages = [], [], set(), set(), set(), set()
        if len(refs) != len(profile["selections"]):
            violations.append({"type": "duplicate_selection"})
        queue, requirements, used_bindings = sorted(refs), [], set()
        while queue:
            ref = queue.pop(0)
            if ref in visited:
                continue
            visited.add(ref)
            if ref not in self.records and ref not in self.components:
                violations.append({"type": "unknown_selection", "id": ref})
                continue
            package = self.package(ref)
            record = self.records[package]
            if record["kind"] in EXCLUDED:
                violations.append({"type": "excluded_source", "id": ref, "kind": record["kind"]})
                continue
            if ref in self.records:
                whole.add(ref)
                components.update(self.by_package[ref])
            else:
                components.add(ref)
            if package in packages:
                continue
            packages.add(package)
            for requirement in record["requirements"]:
                target = requirement["target"]
                candidates = self.provider_candidates(target)
                binding = profile["providers"].get(target)
                item = {"package": package, "target": target,
                        "declaration": deepcopy(requirement), "node": deepcopy(self.nodes[target]),
                        "candidate_providers": candidates, "binding": binding,
                        "satisfied": False}
                requirements.append(item)
                if binding is not None:
                    used_bindings.add(target)
                    # Whole loader bundles are allowed as explicit candidate choices.
                    matches = (binding in candidates or binding in self.records and
                               any(self.package(c) == binding for c in candidates))
                    if not matches:
                        violations.append({"type": "invalid_provider_binding", "target": target,
                                           "binding": binding})
                    else:
                        queue.append(binding)
                blockers.append({"type": "requirement_unverified", "package": package,
                                 "target": target, "binding": binding,
                                 "reason": "Candidate presence is not exact component/version/runtime proof"})
        for target in sorted(profile["providers"].keys() - used_bindings):
            violations.append({"type": "unused_provider_binding", "target": target})
        violations.extend(check_known_selection(self.graph, sorted(components))["violations"])
        resource_overlaps = []
        for kind in ["container_id_overlaps", "chunk_id_overlaps"]:
            for target, members in self.graph.get(kind, {}).items():
                chosen = sorted(components.intersection(members))
                if len(chosen) > 1:
                    resource_overlaps.append({"kind": kind, "target": target, "members": chosen,
                                              "compatibility": "UNKNOWN"})
        if resource_overlaps:
            blockers.append({"type": "resource_overlap_unverified", "groups": len(resource_overlaps)})
        script_overlaps = []
        for target, overlap in self.interfaces["raw_script_target_overlaps"].items():
            members = [deepcopy(m) for m in overlap["members"] if m["package"] in whole]
            if len(members) < 2:
                continue
            same = len({(m["sha256"], m["size"]) for m in members}) == 1
            script_overlaps.append({"target": target, "members": members,
                                    "status": "SAME_SOURCE_BYTES" if same else "DIFFERENT_SOURCE_BYTES",
                                    "deployment_mapping": "UNVERIFIED"})
            blockers.append({"type": "script_provider_mapping_unverified", "target": target,
                             "same_bytes": same})
        keybindings, file_accesses, missing_key_evidence = [], [], []
        for document in self.interfaces.get('documents', []):
            if document['namespace'] != 'raw' or document['package'] not in whole or document['analysis']['type'] != 'lua':
                continue
            analysis = document['analysis']
            identity = {k: deepcopy(document[k]) for k in ['id', 'package', 'module', 'sha256']}
            if 'keybind_semantics' not in analysis or 'file_accesses' not in analysis:
                missing_key_evidence.append(document['id']); continue
            # A query never reserves a key. Unknown wrappers remain visible.
            keybindings.extend({**identity, **deepcopy(call)} for call in analysis['keybind_semantics']
                               if call['role'] != 'QUERY_SPELLING')
            file_accesses.extend({**identity, **deepcopy(call)} for call in analysis['file_accesses'])
        key_groups = {}
        for call in keybindings:
            if call['role'] == 'REGISTER_SPELLING' and call['callee_scope'] == 'BARE' and call['candidate_virtual_key'] is not None:
                key_groups.setdefault(call['candidate_virtual_key'], []).append(call)
        key_overlaps = [{'candidate_virtual_key': key, 'members': members,
                         'compatibility': 'UNKNOWN; literal modifiers do not prove disjoint dispatch/branches/activation'}
                        for key, members in sorted(key_groups.items()) if len(members) > 1]
        if key_overlaps:
            blockers.append({'type': 'keybinding_overlap_unverified', 'base_keys': len(key_overlaps)})
        unresolved_keys = sum(call['chord_status'] == 'UNRESOLVED' for call in keybindings)
        if unresolved_keys:
            blockers.append({'type': 'keybinding_expression_unresolved', 'calls': unresolved_keys})
        writes = sum(call['intent'] != 'READ_SPELLING' for call in file_accesses)
        if writes:
            blockers.append({'type': 'configuration_write_target_unverified', 'calls': writes})
        if missing_key_evidence or whole and 'documents' not in self.interfaces:
            blockers.append({'type': 'keybinding_and_file_access_evidence_missing', 'documents': missing_key_evidence})
        # No path/layout, body, ABI, runtime or save safety proof is supplied here.
        if refs:
            blockers.append({"type": "application_not_verified",
                             "reason": "Raw discovery preview; deployment/ABI/body/DLC/runtime evidence required"})
        provenance = [{"package": key, "content_sha256": self.records[key]["content_sha256"],
                       "kind": self.records[key]["kind"],
                       "source_path": self.records[key]["canonical_path"],
                       "aliases": deepcopy(self.records[key]["aliases"]),
                       "original_page": self.records[key]["permission"].get("source"),
                       "filename_hints": deepcopy(self.records[key]["filename_hints"]),
                       "purpose": "UNKNOWN; exact author description still required",
                       "runtime_compatibility": "NOT_RUN"} for key in sorted(packages)]
        return {"name": profile["name"], "explicit_refs": sorted(refs),
                "closure_refs": sorted(visited), "whole_packages": sorted(whole),
                "components": sorted(components), "source_packages": sorted(packages),
                "requirements": requirements, "script_overlaps": script_overlaps,
                'keybinding_evidence': keybindings, 'keybinding_overlaps': key_overlaps,
                'file_access_evidence': file_accesses,
                "resource_overlaps": resource_overlaps,
                "violations": violations, "blockers": blockers, "provenance": provenance,
                "status": "REJECTED_STATIC" if violations else "REVIEW_REQUIRED" if blockers else
                          "EMPTY_PROFILE_PREVIEW", "can_apply": False,
                "runtime_compatibility": "NOT_VERIFIED"}

    def preview_switch(self, current, requested):
        before, after = self.inspect(current), self.inspect(requested)
        provider_changes = [{"target": key, "before": current["providers"].get(key),
                             "after": requested["providers"].get(key)}
                            for key in sorted(current["providers"].keys() | requested["providers"].keys())
                            if current["providers"].get(key) != requested["providers"].get(key)]
        changed = before["closure_refs"] != after["closure_refs"] or bool(provider_changes)
        return {"current": before, "requested": after,
                "impact": {"add_refs": sorted(set(after["closure_refs"]) - set(before["closure_refs"])),
                           "remove_refs": sorted(set(before["closure_refs"]) - set(after["closure_refs"])),
                           "add_components": sorted(set(after["components"]) - set(before["components"])),
                           "remove_components": sorted(set(before["components"]) - set(after["components"])),
                           "provider_changes": provider_changes,
                           "application": "EXIT_GAME_REQUIRED_OR_UNSUPPORTED" if changed else "NO_SELECTION_CHANGE"},
                "retained_profile": deepcopy(current), "can_apply": False,
                "decision": after["status"],
                "scope": "READONLY_RAW_CONFIGURATION_PREVIEW; no profile save, deployment, game or runtime effect"}


def read_json(path):
    """Refuse duplicate keys, links and concurrent changes in evidence inputs."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024**2:
            raise ValueError("Expected bounded regular metadata")
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if stamp(before) != stamp(os.fstat(fd)) or stamp(before) != stamp(os.lstat(path)):
            raise ValueError("Metadata changed while reading")
        return json.loads(raw, object_pairs_hook=pairs), hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["catalog", "graph", "interfaces", "current", "requested", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    private = Path(__file__).resolve().parents[2] / ".local"
    output = args.output.absolute()
    # Existing owned non-symlink directories only; exclusive create never overwrites.
    if private not in output.parents or any(p.is_symlink() for p in [output, *output.parents]):
        raise ValueError("Preview output must stay in owned private metadata directory")
    if any(p.stat().st_uid != os.getuid() for p in output.parents if p == private or private in p.parents):
        raise ValueError("Unowned preview directory")
    inputs = {name: read_json(getattr(args, name)) for name in
              ["catalog", "graph", "interfaces", "current", "requested"]}
    catalog, graph, interfaces = [inputs[name][0] for name in ["catalog", "graph", "interfaces"]]
    if (graph["catalog_sha256"] != inputs["catalog"][1] or
            interfaces["input_sha256"]["catalog"] != inputs["catalog"][1] or
            graph["inventory_sha256"] != catalog["inventory_sha256"] or
            interfaces["input_sha256"]["inventory"] != catalog["inventory_sha256"]):
        raise ValueError("Discovery snapshots do not share the same catalog")
    planner = ProfilePlanner(catalog, graph, interfaces)
    result = planner.preview_switch(inputs["current"][0], inputs["requested"][0])
    result["input_sha256"] = {name: value[1] for name, value in inputs.items()}
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({"decision": result["decision"], "can_apply": False,
                      "selected_packages": len(result["requested"]["source_packages"]),
                      "known_violations": len(result["requested"]["violations"]),
                      "unverified_conditions": len(result["requested"]["blockers"])}))


if __name__ == "__main__":
    main()
