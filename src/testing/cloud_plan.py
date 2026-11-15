"""Read publisher Cloud declarations; never launch Steam/game or change settings.

This deliberately skips every application's reserved header and all non-target
payloads. It is not an account-cache parser or a general command runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import struct

APP_ID = 3489700
MAX_CACHE = 64 * 1024**2
MAX_PAYLOAD = 4 * 1024**2
MAX_KEYS = 100000
MAX_STRING = 16384
SENSITIVE = re.compile(r'access.?token|password|credential|secret|auth.?ticket|login.?key|api.?key|session', re.I)


class CacheRefused(ValueError):
    """Fixed diagnostic codes only, with no source values in error messages."""


def identity(st):
    return [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


class Reader:
    def __init__(self, stream, size):
        self.stream, self.size = stream, size
        self.ranges = []
        self.requests = 0

    def read(self, size):
        offset = self.stream.tell()
        if size < 0 or offset < 0 or offset + size > self.size:
            raise CacheRefused('READ_BOUNDARY')
        data = self.stream.read(size)
        self.requests += 1
        if self.ranges and self.ranges[-1][1] == offset:
            self.ranges[-1][1] = offset + size
        else:
            self.ranges.append([offset, offset + size])
        if len(data) != size:
            raise CacheRefused('TRUNCATED_READ')
        return data

    def seek(self, offset):
        if not 0 <= offset <= self.size:
            raise CacheRefused('SEEK_BOUNDARY')
        self.stream.seek(offset)

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.read(struct.calcsize('<' + fmt)))[0]

    def string(self):
        value = bytearray()
        for _ in range(MAX_STRING):
            byte = self.read(1)
            if byte == b'\0':
                try:
                    return value.decode('utf-8')
                except UnicodeDecodeError:
                    raise CacheRefused('STRING_ENCODING') from None
            value.extend(byte)
        raise CacheRefused('STRING_LIMIT')


def public_tree(data, keys):
    import io
    reader = Reader(io.BytesIO(data), len(data))
    nodes = 0

    def object_at(depth):
        nonlocal nodes
        if depth > 32:
            raise CacheRefused('NESTING_LIMIT')
        output, seen = {}, set()
        while True:
            kind = reader.number('B')
            if kind == 8:
                return output
            if kind not in (0, 1, 2, 3, 7, 10):
                raise CacheRefused('UNSUPPORTED_VALUE_TYPE')
            if keys is None:
                key = reader.string()
            else:
                index = reader.number('I')
                if index >= len(keys):
                    raise CacheRefused('UNRESOLVED_KEY')
                key = keys[index]
            if not key or len(key) > 1024 or SENSITIVE.search(key):
                raise CacheRefused('UNSAFE_KEY')
            folded = key.casefold()
            if folded in seen:
                raise CacheRefused('AMBIGUOUS_KEY')
            seen.add(folded)
            nodes += 1
            if nodes > MAX_KEYS:
                raise CacheRefused('NODE_LIMIT')
            if kind == 0:
                value = object_at(depth + 1)
            elif kind == 1:
                value = reader.string()
            else:
                value = reader.number({2: 'i', 3: 'f', 7: 'Q', 10: 'q'}[kind])
                if isinstance(value, float) and not math.isfinite(value):
                    raise CacheRefused('NONFINITE_VALUE')
            output[key] = value

    document = object_at(0)
    if reader.stream.tell() != len(data) or set(document) != {'appinfo'}:
        raise CacheRefused('INVALID_DOCUMENT_ROOT')
    app = document['appinfo']
    if not isinstance(app, dict) or str(app.get('appid')) != str(APP_ID):
        raise CacheRefused('PAYLOAD_APP_MISMATCH')
    common, config = app.get('common', {}), app.get('config', {})
    if not isinstance(common, dict) or not isinstance(config, dict):
        raise CacheRefused('INVALID_PUBLIC_SECTION')
    projection = {'common': {}, 'config': {}}
    if 'name' in common:
        projection['common']['name'] = common['name']
    for key in ['launch', 'installdir']:
        if key in config:
            projection['config'][key] = config[key]
    if 'ufs' in app:
        projection['ufs'] = app['ufs']
    validate_projection(projection)
    return projection


def relative_declaration(value):
    if not isinstance(value, str):
        raise CacheRefused('PATH_TYPE')
    path = value.replace('\\', '/')
    if (path.startswith('/') or PureWindowsPath(value).drive or
            '..' in path.split('/') or '\0' in path):
        raise CacheRefused('UNSAFE_DECLARED_PATH')


def validate_projection(projection):
    def visit(value, key=''):
        if isinstance(value, dict):
            for name, item in value.items():
                if SENSITIVE.search(name):
                    raise CacheRefused('SENSITIVE_PUBLIC_KEY')
                visit(name)
                visit(item, name.casefold())
        elif isinstance(value, str):
            if (re.search(r'7656119\d{10}|gh[opsu]_[A-Za-z0-9]+|/home/|/Users/', value) or
                    SENSITIVE.search(value)):
                raise CacheRefused('SENSITIVE_PUBLIC_VALUE')
            if key in {'executable', 'workingdir', 'installdir', 'path', 'subdirectory', 'addpath', 'pattern'}:
                relative_declaration(value)
        elif isinstance(value, (int, float)):
            if 76561190000000000 <= value <= 76561199999999999:
                raise CacheRefused('SENSITIVE_PUBLIC_VALUE')
    visit(projection)
    config = projection['config']
    if ('launch' in config and not isinstance(config['launch'], dict)) or (
            'ufs' in projection and not isinstance(projection['ufs'], dict)):
        raise CacheRefused('INVALID_DECLARATION_STRUCTURE')


def extract(stream, size):
    if not 12 <= size <= MAX_CACHE:
        raise CacheRefused('CACHE_SIZE_LIMIT')
    reader = Reader(stream, size)
    magic, universe = reader.number('I'), reader.number('I')
    if magic not in (0x07564428, 0x07564429) or universe != 1:
        raise CacheRefused('UNSUPPORTED_CACHE_FORMAT')
    version = magic & 255
    table = reader.number('Q') if version == 41 else size
    if not reader.stream.tell() <= table <= size:
        raise CacheRefused('STRING_TABLE_BOUNDARY')
    entries, reserved, other_payloads, targets = 0, [], [], []
    while True:
        if reader.stream.tell() + 4 > table:
            raise CacheRefused('MISSING_ENTRY_TERMINATOR')
        appid = reader.number('I')
        if not appid:
            break
        if reader.stream.tell() + 4 > table:
            raise CacheRefused('TRUNCATED_ENTRY_SIZE')
        length = reader.number('I')
        start, end = reader.stream.tell(), reader.stream.tell() + length
        if length < 60 or end > table:
            raise CacheRefused('ENTRY_SIZE_BOUNDARY')
        reserved.append((start, start + 60))
        payload = (start + 60, end)
        (targets if appid == APP_ID else other_payloads).append(payload)
        entries += 1
        if entries > MAX_KEYS:
            raise CacheRefused('ENTRY_LIMIT')
        reader.seek(end)  # No token, header hash, or unrelated payload read.
    if reader.stream.tell() != table or len(targets) != 1:
        raise CacheRefused('AMBIGUOUS_TARGET_OR_FOOTER')
    keys = None
    if version == 41:
        count = reader.number('I')
        if not 1 <= count <= MAX_KEYS:
            raise CacheRefused('KEY_TABLE_LIMIT')
        keys = [reader.string() for _ in range(count)]
        if reader.stream.tell() != size:
            raise CacheRefused('KEY_TABLE_TRAILING_BYTES')
    start, end = targets[0]
    if not 0 < end - start <= MAX_PAYLOAD:
        raise CacheRefused('TARGET_PAYLOAD_LIMIT')
    reader.seek(start)
    projection = public_tree(reader.read(end - start), keys)
    forbidden = reserved + other_payloads
    if any(left < stop and begin < right for left, right in reader.ranges for begin, stop in forbidden):
        raise CacheRefused('READ_AUDIT_BOUNDARY')
    return {'appid': APP_ID, 'format_version': version, 'publisher': projection,
            'read_audit': {'entries': entries, 'reserved_headers_skipped': len(reserved),
                           'non_target_payloads_skipped': len(other_payloads),
                           'requests': reader.requests, 'requested_ranges': reader.ranges,
                           'reserved_header_bytes_requested': 0, 'non_target_payload_bytes_requested': 0},
            'authority': 'CACHED_PUBLISHER_DECLARATION_ONLY; current server and runtime NOT_VERIFIED'}


def inspect_cache(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.name != 'appinfo.vdf' or path.parent.name != 'appcache':
        raise CacheRefused('DESIGNATED_CACHE_PATH_REQUIRED')
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise CacheRefused('LINKED_CACHE_ANCESTOR')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise CacheRefused('CACHE_NOT_REGULAR')
        report = extract(stream, before.st_size)
        after = os.fstat(stream.fileno())
        if identity(before) != identity(after) or identity(after) != identity(path.lstat()):
            raise CacheRefused('CACHE_CHANGED_DURING_READ')
    report['cache_identity'] = identity(before)
    return report


def route_report(report):
    ufs = report['publisher'].get('ufs')
    return {'publisher_ufs': 'DECLARED' if ufs else 'NOT_PRESENT_IN_CACHE',
            'fresh_game_prefix_with_existing_client': 'INDETERMINATE: client-mediated Cloud and installed-game restart not isolated',
            'private_authenticated_client': 'INDETERMINATE: legitimate authentication, separate local userdata, Cloud and activation boundaries not established',
            'can_launch_game': False, 'can_apply': False,
            'observed_cloud_invocation': 'NOT_RUN', 'local_and_cloud_save_isolation': 'NOT_ESTABLISHED',
            'runtime_abi_and_mod_combinations': 'NOT_RUN',
            'next_requirement': 'Establish exact legitimate client/game/local-save/Cloud protection route under separate authority before real launch; no settings/session/DRM/API change authorized here.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).absolute()
    repository = Path(__file__).resolve().parents[2]
    parent = repository / '.local/r04/cloud-plan'
    if output.parent != parent or output.suffix != '.json':
        raise CacheRefused('OWNED_PRIVATE_OUTPUT_REQUIRED')
    for directory in output.parents:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise CacheRefused('LINKED_OUTPUT_ANCESTOR')
    report = inspect_cache(Path(args.cache))
    report['route'] = route_report(report)
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    with output.open('x') as stream:
        stream.write(serialized)
    print(json.dumps({'appid': APP_ID, 'publisher_ufs': report['route']['publisher_ufs'],
                      'metadata_only': True, 'can_launch_game': False,
                      'projection_sha256': hashlib.sha256(serialized.encode()).hexdigest()}))


if __name__ == '__main__':
    main()
