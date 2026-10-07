import struct
import unittest

from src.policy.resources import NONE, directory_paths, overlaps, toc_resources
from src.policy.static import TOC_MAGIC


def string(value):
    data = value.encode() + b"\0"
    return struct.pack("<i", len(data)) + data


def fixture(cyclic=False):
    directory = string("../../../")
    directory += struct.pack("<I4I", 1, NONE, NONE, NONE, 0)
    directory += struct.pack("<I3I", 1, 0, 0 if cyclic else NONE, 0)
    directory += struct.pack("<I", 1) + string("Test.uasset")
    header = bytearray(0x90)
    header[:16], header[16], header[80] = TOC_MAGIC, 2, 8
    struct.pack_into("<II", header, 20, 0x90, 1)
    struct.pack_into("<I", header, 32, 12)
    struct.pack_into("<I", header, 48, len(directory))
    chunk = b"\x01" + b"\0" * 10 + b"\x02"
    return bytes(header) + chunk + bytes(5) + (42).to_bytes(5, "big") + directory + b"\x01" * 32 + b"\0"


class ResourceTests(unittest.TestCase):
    def test_real_layout_and_stored_hash_scope(self):
        meta = toc_resources(fixture())
        self.assertEqual(meta["chunks"][0]["path"], "../../../Test.uasset")
        self.assertEqual(meta["chunks"][0]["size"], 42)
        self.assertEqual(meta["chunks"][0]["resource_key"], "test.uasset")
        self.assertIn("NOT_REHASHED", meta["hash_scope"])

    def test_malformed_metadata_is_rejected(self):
        for data in (fixture()[:-1], fixture() + b"\0", fixture(cyclic=True)):
            with self.assertRaises(ValueError):
                toc_resources(data)
        encrypted = bytearray(fixture())
        encrypted[80] |= 2
        with self.assertRaises(ValueError):
            toc_resources(bytes(encrypted))

    def test_bad_directory_indices_cannot_become_paths(self):
        data = string("../../../") + struct.pack("<I4I", 1, NONE, NONE, NONE, 0)
        data += struct.pack("<I3I", 1, 0, NONE, 99) + struct.pack("<I", 1) + string("bad.uasset")
        with self.assertRaises(ValueError):
            directory_paths(data, 1)

    def test_overlap_is_not_runtime_rule_and_zero_hash_stays_unknown(self):
        base = toc_resources(fixture())
        containers = [{"id": name, "metadata": base} for name in ["original", "converted"]]
        group = next(iter(overlaps(containers, "resource_key").values()))
        self.assertEqual(group["status"], "SAME_STORED_HASH_AND_SIZE")
        self.assertIn("UNKNOWN", group["runtime_rule"])
        base["chunks"][0]["stored_hash"] = "0" * 64
        self.assertEqual(next(iter(overlaps(containers, "id").values()))["status"], "UNKNOWN_ZERO_HASH")


if __name__ == "__main__":
    unittest.main()
