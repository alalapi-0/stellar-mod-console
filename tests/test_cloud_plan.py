import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from src.testing import cloud_plan as c


def fixture(version=41, *, duplicate=False, missing=False, tree=None, numeric_kind=None):
    tree = tree or {'appinfo': {'appid': str(c.APP_ID), 'common': {'name': 'Synthetic Game', 'ignored': 'not exported'},
        'config': {'installdir': 'Game', 'launch': {'0': {'executable': r'SB\Game.exe', 'arguments': '-dx12'}}},
        'ufs': {'savefiles': {'0': {'root': 'WinAppDataLocal', 'path': 'SB/Saved/SaveGames/{64BitSteamID}', 'pattern': '*.sav'}}}}}
    keys = []

    def encode(obj):
        output = bytearray()
        for key, value in obj.items():
            if key not in keys:
                keys.append(key)
            if isinstance(value, dict):
                kind = 0
            elif isinstance(value, int):
                kind = numeric_kind or (2 if -2**31 <= value < 2**31 else 7)
            else:
                kind = 3 if isinstance(value, float) else 1
            output.append(kind)
            output.extend(struct.pack('<I', keys.index(key)) if version == 41 else key.encode() + b'\0')
            output.extend(encode(value) if kind == 0 else
                          struct.pack('<' + {2: 'i', 3: 'f', 7: 'Q', 10: 'q'}[kind], value) if kind != 1 else
                          value.encode() + b'\0')
        output.append(8)
        return bytes(output)

    payload = encode(tree)
    file_header = 16 if version == 41 else 8
    entries, forbidden, location = [], [], file_header
    rows = [(7, b'UNRELATED_PAYLOAD\0')]
    if not missing:
        rows.append((c.APP_ID, payload))
    if duplicate:
        rows.append((c.APP_ID, payload))
    rows.append((8, b'OTHER_PAYLOAD\0'))
    for appid, data in rows:
        # Secret/hash sentinels deliberately never interpreted by the reader.
        reserved = b'R' * 8 + b'SECRET!!' + b'H' * 20 + b'N' * 4 + b'J' * 20
        entry = struct.pack('<II', appid, len(reserved) + len(data)) + reserved + data
        forbidden.append((location + 8, location + 68))
        if appid != c.APP_ID:
            forbidden.append((location + 68, location + len(entry)))
        entries.append(entry)
        location += len(entry)
    body = b''.join(entries) + struct.pack('<I', 0)
    table = struct.pack('<I', len(keys)) + b''.join(k.encode() + b'\0' for k in keys)
    header = struct.pack('<II', 0x07564400 + version, 1)
    if version == 41:
        header += struct.pack('<Q', file_header + len(body))
    return header + body + (table if version == 41 else b''), forbidden


class AuditStream(io.BytesIO):
    def __init__(self, data, forbidden):
        super().__init__(data)
        self.forbidden, self.requests = forbidden, []

    def read(self, size=-1):
        start = self.tell()
        if size < 0 or any(start < right and left < start + size for left, right in self.forbidden):
            raise AssertionError('Requested a forbidden source byte')
        self.requests.append((start, size))
        return super().read(size)


class CloudPlanTests(unittest.TestCase):
    def test_v40_v41_never_request_any_reserved_or_other_payload_byte(self):
        for version in [40, 41]:
            data, forbidden = fixture(version)
            stream = AuditStream(data, forbidden)
            out = c.extract(stream, len(data))
            self.assertEqual(out['format_version'], version)
            self.assertEqual(out['read_audit']['reserved_headers_skipped'], 3)
            self.assertEqual(out['read_audit']['non_target_payloads_skipped'], 2)
            self.assertEqual(out['publisher']['common'], {'name': 'Synthetic Game'})
            self.assertNotIn('ignored', json.dumps(out))
            self.assertFalse(c.route_report(out)['can_launch_game'])

    def test_reject_bad_version_universe_size_and_missing_or_duplicate_target(self):
        data, _ = fixture()
        cases = [struct.pack('<I', 0x07564427) + data[4:], data[:4] + struct.pack('<I', 2) + data[8:],
                 data[:20] + struct.pack('<I', 59) + data[24:], fixture(missing=True)[0], fixture(duplicate=True)[0],
                 data[:20] + struct.pack('<I', 0xffffffff) + data[24:], data[:5]]
        for value in cases:
            with self.assertRaises(c.CacheRefused):
                c.extract(io.BytesIO(value), len(value))
        with self.assertRaises(c.CacheRefused):
            c.extract(io.BytesIO(data), c.MAX_CACHE + 1)

    def test_table_cannot_point_into_reserved_header(self):
        data, forbidden = fixture()
        data = data[:8] + struct.pack('<Q', forbidden[0][0] + 8) + data[16:]
        with self.assertRaises(c.CacheRefused):
            c.extract(AuditStream(data, forbidden), len(data))

    def test_malformed_payload_unknown_type_key_and_terminator(self):
        cases = [b'\x09', b'\0bad\0\x08', b'\0appinfo\0\x01appid\0',
                 b'\0appinfo\0\x01appid\0' + str(c.APP_ID).encode() + b'\0\x08\x08x']
        for data in cases:
            with self.assertRaises(c.CacheRefused):
                c.public_tree(data, None)
        with self.assertRaises(c.CacheRefused):
            c.public_tree(b'\0' + struct.pack('<I', 99), ['appinfo'])

    def test_duplicate_keys_case_aliases_appid_and_sensitive_fields_refused(self):
        appid = str(c.APP_ID).encode()
        cases = [b'\0appinfo\0\x01appid\0' + appid + b'\0\x01APPID\0' + appid + b'\0\x08\x08',
                 fixture(tree={'appinfo': {'appid': '7'}})[0],
                 fixture(tree={'appinfo': {'appid': str(c.APP_ID), 'access_token': 'SYNTHETIC'}})[0],
                 fixture(tree={'appinfo': {'appid': str(c.APP_ID), 'config': {'launch': {'0': {'arguments': '-password SYNTHETIC'}}}}})[0]]
        with self.assertRaises(c.CacheRefused):
            c.public_tree(cases[0], None)
        for data in cases[1:]:
            with self.assertRaises(c.CacheRefused):
                c.extract(io.BytesIO(data), len(data))

    def test_export_paths_and_actual_account_identifier_refused(self):
        for path in ['../old', r'C:\personal', '/home/synthetic/data', r'SB\..\old', 'SB/76561190000000001']:
            data, _ = fixture(tree={'appinfo': {'appid': str(c.APP_ID), 'ufs': {'savefiles': {'0': {'path': path}}}}})
            with self.assertRaises(c.CacheRefused):
                c.extract(io.BytesIO(data), len(data))

    def test_depth_string_and_node_bounds(self):
        nested = {}; cursor = nested
        for _ in range(40):
            cursor['child'] = {}; cursor = cursor['child']
        data, _ = fixture(tree={'appinfo': {'appid': str(c.APP_ID), 'extra': nested}})
        with self.assertRaises(c.CacheRefused):
            c.extract(io.BytesIO(data), len(data))
        with self.assertRaises(c.CacheRefused):
            c.Reader(io.BytesIO(b'x' * c.MAX_STRING), c.MAX_STRING).string()
        data, _ = fixture()
        with patch.object(c, 'MAX_KEYS', 3), self.assertRaises(c.CacheRefused):
            c.extract(io.BytesIO(data), len(data))

    def test_numeric_account_identifiers_and_key_identifiers_refused(self):
        for version in [40, 41]:
            for kind, value in [(7, 76561190000000001), (10, 76561190000000001),
                                (3, float(76561190000000001))]:
                data, forbidden = fixture(version, numeric_kind=kind, tree={
                    'appinfo': {'appid': str(c.APP_ID), 'ufs': {'owner': value}}})
                with self.assertRaises(c.CacheRefused):
                    c.extract(AuditStream(data, forbidden), len(data))
            data, forbidden = fixture(version, tree={'appinfo': {
                'appid': str(c.APP_ID), 'ufs': {'76561190000000001': 'synthetic'}}})
            with self.assertRaises(c.CacheRefused):
                c.extract(AuditStream(data, forbidden), len(data))
            data, forbidden = fixture(version, tree={'appinfo': {
                'appid': str(c.APP_ID), 'ufs': {'quota': 600000000, 'maxnumfiles': 20}}})
            self.assertEqual(c.extract(AuditStream(data, forbidden), len(data))['publisher']['ufs'],
                             {'quota': 600000000, 'maxnumfiles': 20})

    def test_regular_fixed_cache_path_and_symlink_ancestor_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cache = root / 'appcache'; cache.mkdir()
            path = cache / 'appinfo.vdf'; path.write_bytes(fixture()[0])
            self.assertEqual(c.inspect_cache(path)['appid'], c.APP_ID)
            other = cache / 'other.vdf'; other.write_bytes(fixture()[0])
            with self.assertRaises(c.CacheRefused):
                c.inspect_cache(other)
            path.unlink(); path.symlink_to(other)
            with self.assertRaises(OSError):
                c.inspect_cache(path)
            linked = root / 'link'; linked.symlink_to(cache, target_is_directory=True)
            with self.assertRaises(c.CacheRefused):
                c.inspect_cache(linked / 'appinfo.vdf')
            path.unlink(); os.mkfifo(path)
            with self.assertRaises(c.CacheRefused):
                c.inspect_cache(path)

    def test_cache_concurrent_change_and_inode_replacement_refused(self):
        for change in ['timestamp', 'size', 'replacement']:
            with tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / 'appcache'; cache.mkdir(); path = cache / 'appinfo.vdf'; path.write_bytes(fixture()[0])
                original = c.extract

                def changing(stream, size):
                    result = original(stream, size)
                    if change == 'timestamp':
                        st = path.stat(); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
                    elif change == 'size':
                        with path.open('ab') as f:
                            f.write(b'x')
                    else:
                        new = cache / 'replacement'; new.write_bytes(fixture()[0]); new.replace(path)
                    return result

                with patch.object(c, 'extract', changing), self.assertRaises(c.CacheRefused):
                    c.inspect_cache(path)


if __name__ == '__main__':
    unittest.main()
