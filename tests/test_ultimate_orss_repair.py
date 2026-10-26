import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.catalog import ultimate_orss_repair as module


class UltimateORSSRepairTests(unittest.TestCase):
    def setUp(self):
        labels = ["Normal", "With Pubic hair", "More Pubic hair", "Big Areola",
                  "Black Nails", "EVE Scarlet Pale Body"]
        self.document = [{"UniqueFitID": "UltimateNudeEve", "preserve": [17, "fixture"],
                          "UserConfigs": {"TextureOptions": [{}, {}, {}, {},
                              {"DisplayName": "Body Normal", "OptionNames": labels},
                              {"DisplayName": "Body ORSS", "ControlledBy": "Body Normal",
                               "OptionNames": labels[:2] + labels[3:],
                               "Textures": [f"fixture/{i}" for i in range(6)], "Value": 0}]}}]
        self.before = (json.dumps(self.document, indent=2) + "\n").encode()
        start = self.before.index(b'"DisplayName": "Body ORSS"')
        self.after = self.before[:start] + self.before[start:].replace(
            b'            "Big Areola",\n',
            b'            "More Pubic hair",\n            "Big Areola",\n', 1)
        self.pins = patch.multiple(module, SOURCE_SHA256=hashlib.sha256(self.before).hexdigest(),
                                   REPAIRED_SHA256=hashlib.sha256(self.after).hexdigest())

    def test_missing_label_is_inserted_without_changing_resources_or_other_fields(self):
        with self.pins:
            result = module.repair(self.before)
        actual = json.loads(result)
        expected = json.loads(self.before)
        expected[0]["UserConfigs"]["TextureOptions"][5]["OptionNames"].insert(2, "More Pubic hair")
        self.assertEqual(actual, expected)
        self.assertEqual(result, self.after)
        self.assertEqual(len(result) - len(self.before), 31)

    def test_repaired_preimage_is_idempotent_and_other_bytes_are_refused(self):
        with self.pins:
            self.assertEqual(module.repair(self.after), self.after)
            with self.assertRaisesRegex(ValueError, "Unknown preimage"):
                module.repair(self.before + b" ")

    def test_inconsistent_author_reference_is_refused_even_with_fixture_pin(self):
        self.document[0]["UserConfigs"]["TextureOptions"][4]["OptionNames"][2] = "Different label"
        changed = json.dumps(self.document, indent=2).encode()
        with patch.object(module, "SOURCE_SHA256", hashlib.sha256(changed).hexdigest()):
            with self.assertRaisesRegex(ValueError, "paired author"):
                module.repair(changed)

    def test_cli_does_not_overwrite_an_existing_output(self):
        with tempfile.TemporaryDirectory() as directory, self.pins:
            source = Path(directory) / "source.json"
            destination = Path(directory) / "candidate.json"
            source.write_bytes(self.before)
            destination.write_bytes(b"preserved")
            with self.assertRaises(FileExistsError):
                module.main([str(source), str(destination)])
            self.assertEqual(source.read_bytes(), self.before)
            self.assertEqual(destination.read_bytes(), b"preserved")


if __name__ == "__main__":
    unittest.main()
