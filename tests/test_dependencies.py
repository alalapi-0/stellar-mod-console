import unittest

from src.catalog.dependencies import components, discover


class DependencyTests(unittest.TestCase):
    def test_alias_is_one_record_and_unknown_does_not_mean_compatible(self):
        rows = [{"id": f"input-{i}", "path": f"/synthetic/mod-{i}.zip", "sha256": "same-digest",
                 "integrity": "DECODED", "attribution": "stellar_content_candidate",
                 "entries": [{"path": "suit.dekcns.json", "sha256": "config-digest"}]} for i in range(2)]
        facts = {"mods": {}, "dependency_nodes": [{"id": "mod:1496", "status": "CANDIDATE_NOT_RUNTIME_VERIFIED"}]}
        result = discover({"sources": rows}, facts)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["summary"]["input_references"], 2)
        record = result["records"][0]
        self.assertEqual(record["runtime_compatibility"], "NOT_RUN")
        self.assertFalse(record["permission"]["redistribute"])
        self.assertTrue(any(r["confidence"] == "UNKNOWN" for r in record["requirements"]))

    def test_partial_download_is_never_an_alias_of_complete_content(self):
        rows = [{"id": "one", "path": "/synthetic/a.zip", "sha256": "digest", "integrity": "DECODED", "attribution": "stellar_mod"},
                {"id": "two", "path": "/synthetic/a.crdownload", "sha256": "digest", "integrity": "INCOMPLETE", "attribution": "incomplete_download"}]
        result = discover({"sources": rows}, {"mods": {}, "dependency_nodes": []})
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["summary"]["relevant_candidate_records"], 1)

    def test_missing_dependency_reference_is_rejected(self):
        row = {"id": "one", "path": "/synthetic/a.zip", "sha256": "digest", "integrity": "DECODED", "attribution": "stellar_mod",
               "entries": [{"path": "mod.dekcns.json", "sha256": "config"}]}
        with self.assertRaisesRegex(ValueError, "graph references"):
            discover({"sources": [row]}, {"mods": {}, "dependency_nodes": []})

    def test_partial_triple_does_not_pass_container_layout(self):
        parts = components([{"path": "SB\\Content\\Paks\\LogicMods\\Tool_P.utoc", "sha256": "toc"},
                            {"path": "SB/Content/Paks/LogicMods/Tool_P.ucas", "sha256": "data"}])
        self.assertEqual(len(parts["containers"]), 1)
        self.assertEqual(parts["containers"][0]["layout"], "INCOMPLETE_OR_NONSTANDARD")


if __name__ == "__main__":
    unittest.main()
