import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.policy.profiles import ProfilePlanner, read_json


def profile(*refs, providers=None):
    return {"schema_version": 1, "name": "test", "selections": list(refs),
            "providers": providers or {}}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        def record(key, kind="container_mod", requires=(), mod=None, digest="a"):
            return {"id": key, "kind": kind, "requirements": [{"target": t} for t in requires],
                    "content_sha256": digest, "filename_hints": {"mod_id": mod},
                    "canonical_path": "/synthetic/" + key, "aliases": [], "permission": {"source": None}}
        self.catalog = {"records": [record("a"), record("b"), record("clothes", requires=["mod:1"]),
                                    record("base", "loader_bundle", ["mod:2"], 1),
                                    record("alternate", "loader_bundle", mod=1),
                                    record("extension", "lua_mod", ["mod:1"], 2),
                                    record("partial", "incomplete_download")],
                        "dependency_nodes": [{"id": "mod:1", "status": "UNKNOWN"},
                                             {"id": "mod:2", "status": "UNKNOWN"}]}
        self.graph = {"variants": [{"id": "a1", "package": "a"}, {"id": "b1", "package": "b"},
                                   {"id": "loader1", "package": "base", "component": "UE4SS loader"},
                                   {"id": "loader2", "package": "alternate", "component": "UE4SS loader"}],
                      "known_exclusive_groups": [{"id": "outfit", "members": ["a1", "b1"], "max_enabled": 1},
                                                 {"id": "loader", "members": ["loader1", "loader2"], "max_enabled": 1}],
                      "container_id_overlaps": {"c": ["a1", "b1"]}, "chunk_id_overlaps": {}}
        self.interfaces = {"raw_script_target_overlaps": {"mods/shared/scripts/main.lua": {
            "members": [{"package": "base", "sha256": "x", "size": 1},
                        {"package": "extension", "sha256": "y", "size": 1}]}}}
        self.planner = ProfilePlanner(self.catalog, self.graph, self.interfaces)

    def test_switch_replaces_exclusive_component_but_never_applies(self):
        current, requested = profile("a1"), profile("b1")
        original = copy.deepcopy((current, requested))
        result = self.planner.preview_switch(current, requested)
        self.assertEqual(result["impact"]["remove_components"], ["a1"])
        self.assertEqual(result["impact"]["add_components"], ["b1"])
        self.assertEqual(result["impact"]["application"], "EXIT_GAME_REQUIRED_OR_UNSUPPORTED")
        self.assertFalse(result["requested"]["violations"])
        self.assertFalse(result["can_apply"])
        self.assertEqual((current, requested), original)
        self.assertEqual(result["retained_profile"], current)
        result["retained_profile"]["selections"].append("mutated")
        self.assertEqual(current, original[0])

    def test_bad_union_and_unknown_duplicate_incomplete_refs_are_rejected(self):
        for requested in [profile("a", "b1"), profile("loader1", "loader2"),
                          profile("missing"), profile("a1", "a1"), profile("partial")]:
            with self.subTest(requested=requested):
                result = self.planner.preview_switch(profile("a1"), requested)
                self.assertEqual(result["decision"], "REJECTED_STATIC")
                self.assertEqual(result["retained_profile"], profile("a1"))
                self.assertFalse(result["can_apply"])

    def test_dependency_candidates_do_not_autopick_or_claim_satisfied(self):
        result = self.planner.inspect(profile("clothes"))
        self.assertEqual(result["requirements"][0]["candidate_providers"], ["alternate", "base"])
        self.assertEqual(result["closure_refs"], ["clothes"])
        self.assertFalse(result["requirements"][0]["satisfied"])
        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_explicit_transitive_cycle_closes_and_checks_provider_exclusion(self):
        providers = {"mod:1": "base", "mod:2": "extension"}
        result = self.planner.inspect(profile("clothes", providers=providers))
        self.assertEqual(result["closure_refs"], ["base", "clothes", "extension"])
        self.assertFalse(result["violations"])
        self.assertEqual(len(result["requirements"]), 3)
        self.assertTrue(all(not r["satisfied"] for r in result["requirements"]))
        bad = self.planner.inspect(profile("clothes", "loader2", providers=providers))
        self.assertTrue(any(v.get("group") == "loader" for v in bad["violations"]))

    def test_invalid_and_unused_provider_bindings_are_not_hidden(self):
        for requested in [profile("clothes", providers={"mod:1": "a"}),
                          profile("a1", providers={"mod:1": "base"}),
                          profile("clothes", providers={"mod:1": "missing"})]:
            self.assertEqual(self.planner.inspect(requested)["status"], "REJECTED_STATIC")

    def test_loader_component_does_not_select_bundle_scripts(self):
        leaf = self.planner.inspect(profile("loader1", "extension"))
        whole = self.planner.inspect(profile("base", "extension"))
        self.assertEqual(leaf["whole_packages"], ["extension"])
        self.assertEqual(leaf["script_overlaps"], [])
        self.assertEqual(whole["script_overlaps"][0]["status"], "DIFFERENT_SOURCE_BYTES")
        self.assertFalse(whole["violations"])  # mapping uncertainty is not a proven hard conflict
        self.assertFalse(whole["can_apply"])
        self.interfaces["raw_script_target_overlaps"]["mods/shared/scripts/main.lua"]["members"][1]["sha256"] = "x"
        same = self.planner.inspect(profile("base", "extension"))
        self.assertEqual(same["script_overlaps"][0]["status"], "SAME_SOURCE_BYTES")
        self.assertEqual(same["status"], "REVIEW_REQUIRED")

    def test_resource_overlap_remains_unknown_without_hard_rule(self):
        self.graph["known_exclusive_groups"] = []
        result = self.planner.inspect(profile("a1", "b1"))
        self.assertFalse(result["violations"])
        self.assertEqual(result["resource_overlaps"][0]["compatibility"], "UNKNOWN")
        self.assertFalse(result["can_apply"])

    def test_whole_bundle_keeps_intra_package_script_target_ambiguity(self):
        members = self.interfaces["raw_script_target_overlaps"]["mods/shared/scripts/main.lua"]["members"]
        members[1]["package"] = "base"
        result = self.planner.inspect(profile("base"))
        self.assertEqual(result["script_overlaps"][0]["status"], "DIFFERENT_SOURCE_BYTES")
        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_provider_change_is_an_impact_even_with_same_selected_refs(self):
        refs = ("clothes", "base", "alternate", "extension")
        current = profile(*refs, providers={"mod:1": "base"})
        requested = profile(*refs, providers={"mod:1": "alternate"})
        result = self.planner.preview_switch(current, requested)
        self.assertEqual(result["impact"]["add_refs"], [])
        self.assertEqual(len(result["impact"]["provider_changes"]), 1)
        self.assertEqual(result["impact"]["application"], "EXIT_GAME_REQUIRED_OR_UNSUPPORTED")

    def test_malformed_schema_duplicate_evidence_and_links_refuse(self):
        bad = profile("a1"); bad["selections"] = "a1"
        with self.assertRaises(ValueError):
            self.planner.inspect(bad)
        bad = profile(); bad["schema_version"] = True
        with self.assertRaises(ValueError):
            self.planner.inspect(bad)
        self.catalog["records"].append(copy.deepcopy(self.catalog["records"][0]))
        with self.assertRaises(ValueError):
            ProfilePlanner(self.catalog, self.graph, self.interfaces)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p.json"; path.write_text('{"x":1,"x":2}')
            with self.assertRaises(ValueError):
                read_json(path)
            path.write_text(json.dumps(profile("a1")))
            link = Path(directory) / "link"; link.symlink_to(path)
            with self.assertRaises(OSError):
                read_json(link)
            self.assertEqual(read_json(path)[0], profile("a1"))


if __name__ == "__main__":
    unittest.main()
