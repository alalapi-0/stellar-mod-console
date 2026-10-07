import struct
import unittest

from src.policy.static import TOC_MAGIC, check_known_selection, toc_identifiers


class StaticPolicyTests(unittest.TestCase):
    def test_known_bad_selection_rejected_and_valid_is_not_runtime_pass(self):
        graph = {"variants": [{"id": x} for x in ["atool-default", "atool-k2", "cns"]],
                 "known_exclusive_groups": [{"id": "atool", "members": ["atool-default", "atool-k2"], "max_enabled": 1}]}
        bad = check_known_selection(graph, ["atool-default", "atool-k2"])
        self.assertEqual(bad["status"], "REJECTED_STATIC")
        valid = check_known_selection(graph, ["atool-default", "cns"])
        self.assertFalse(valid["violations"])
        self.assertFalse(valid["can_apply"])
        self.assertEqual(valid["runtime_compatibility"], "NOT_VERIFIED")
        self.assertEqual(check_known_selection(graph, ["missing"])["status"], "REJECTED_STATIC")

    def test_corrupt_or_future_toc_is_never_parsed_as_success(self):
        data = bytearray(0x90 + 12)
        data[:16] = TOC_MAGIC
        data[16] = 3
        struct.pack_into("<II", data, 20, 0x90, 1)
        struct.pack_into("<Q", data, 56, 0x12345678)
        data[0x90:] = bytes(range(12))
        parsed = toc_identifiers(data)
        self.assertEqual(parsed["container_id"], "0000000012345678")
        self.assertEqual(parsed["chunk_ids"], [bytes(range(12)).hex()])
        with self.assertRaises(ValueError):
            toc_identifiers(data[:-1])
        data[16] = 255
        with self.assertRaises(ValueError):
            toc_identifiers(data)


if __name__ == "__main__":
    unittest.main()
