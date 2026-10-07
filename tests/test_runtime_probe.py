import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from src.testing.runtime_probe import InvalidImage, PEReader, inspect_file


def image(bits=64, delay_va=False):
    """Independent PE fixture with normal/name, ordinal and delay imports."""
    data = bytearray(0x800)
    def put(offset, fmt, *values):
        struct.pack_into('<' + fmt, data, offset, *values)
    data[:2] = b'MZ'; put(0x3c, 'I', 0x80); data[0x80:0x84] = b'PE\0\0'
    optional = 0x98
    optional_size, directories = (240, 112) if bits == 64 else (224, 96)
    put(0x84, 'HH', 0x8664 if bits == 64 else 0x14c, 1)
    put(0x94, 'H', optional_size)
    put(optional, 'H', 0x20b if bits == 64 else 0x10b)
    base = 0x400000
    put(optional + (24 if bits == 64 else 28), 'Q' if bits == 64 else 'I', base)
    put(optional + 60, 'I', 0x200)
    put(optional + directories - 4, 'I', 16)
    put(optional + directories + 8, 'II', 0x1000, 40)
    put(optional + directories + 13 * 8, 'II', 0x1040, 64)
    put(optional + optional_size + 8, 'IIII', 0x600, 0x1000, 0x600, 0x200)
    put(0x200, '5I', 0x1080, 0, 0, 0x1100, 0x1080)
    va = base if delay_va else 0
    put(0x240, '8I', 0 if delay_va else 1, 0x1140 + va, 0,
        0x10a0 + va, 0x10a0 + va, 0, 0, 0)
    fmt = 'QQQ' if bits == 64 else 'III'
    put(0x280, fmt, 0x1120, (1 << (bits - 1)) | 7, 0)
    put(0x2a0, 'QQ' if bits == 64 else 'II', 0x1160 + va, 0)
    for offset, value in [(0x300, b'kernel32.dll\0'), (0x322, b'CreateFileW\0'),
                          (0x340, b'd3d12.dll\0'), (0x362, b'D3D12CreateDevice\0')]:
        data[offset:offset + len(value)] = value
    return data


class RuntimeProbeTests(unittest.TestCase):
    def test_32_and_64_bit_name_ordinal_and_delay_imports(self):
        for bits in (32, 64):
            report = PEReader(image(bits)).report()
            self.assertEqual(report['bits'], bits)
            self.assertEqual(report['imports'], [{'dll': 'kernel32.dll',
                'symbols': [{'name': 'CreateFileW'}, {'ordinal': 7}]}])
            self.assertEqual(report['delay_imports'], [{'dll': 'd3d12.dll',
                'symbols': [{'name': 'D3D12CreateDevice'}]}])
            self.assertEqual(report['runtime_resolution'], 'NOT_RUN')
        self.assertEqual(PEReader(image(32, delay_va=True)).report()['delay_imports'][0]['dll'], 'd3d12.dll')

    def test_absent_import_tables_are_distinct_from_invalid_tables(self):
        data = image(); data[0x110:0x118] = b'\0' * 8
        self.assertEqual(PEReader(data).report()['imports'], [])
        data = image(); struct.pack_into('<II', data, 0x110, 0, 40)
        with self.assertRaises(InvalidImage): PEReader(data).report()

    def test_malformed_offsets_and_truncations_refuse(self):
        mutations = [(0x3c, 'I', 0x100000), (0x94, 'H', 100),
                     (0x104, 'I', 99), (0x110, 'II', 0x15ff, 40),
                     (0x110, 'II', 0x1000, 20), (0x20c, 'I', 0x5000),
                     (0x240, 'I', 2), (0x280, 'Q', 0x5000)]
        for offset, fmt, *values in mutations:
            data = image(); struct.pack_into('<' + fmt, data, offset, *values)
            with self.assertRaises(InvalidImage, msg=str((offset, values))): PEReader(data).report()
        for size in (1, 0x80, 0x98, 0x180, 0x7ff):
            with self.assertRaises(InvalidImage): PEReader(image()[:size]).report()

    def test_unbacked_and_ambiguous_section_mapping_refuse(self):
        data = image(); struct.pack_into('<I', data, 0x190, 0x700)
        reader = PEReader(data)
        with self.assertRaises(InvalidImage): reader.at(0x1600, 1)
        data = image(); struct.pack_into('<HH', data, 0x84, 0x8664, 2)
        data[0x1b0:0x1d8] = data[0x188:0x1b0]
        with self.assertRaises(InvalidImage): PEReader(data).report()

    def test_regular_file_read_is_unchanged_and_symlink_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'owned.exe'; path.write_bytes(image())
            before = path.stat()
            report = inspect_file(path)
            self.assertEqual(len(report['sha256']), 64)
            self.assertEqual(path.read_bytes(), image())
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
            link = Path(temporary) / 'link.exe'; link.symlink_to(path)
            with self.assertRaises(OSError): inspect_file(link)

    def test_concurrent_replacement_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'owned.exe'; path.write_bytes(image())
            original = PEReader.report
            def replace(reader):
                report = original(reader)
                new = path.with_suffix('.new'); new.write_bytes(image())
                os.replace(new, path)
                return report
            with patch.object(PEReader, 'report', replace), self.assertRaises(InvalidImage):
                inspect_file(path)


if __name__ == '__main__':
    unittest.main()
