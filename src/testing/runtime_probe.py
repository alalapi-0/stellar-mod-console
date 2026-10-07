"""Read PE dependencies without loading or executing the inspected image.

Import tables describe static requirements, never successful runtime resolution.
The bounded reader refuses incomplete, ambiguous or unsupported data instead of
turning a failed parse into an empty dependency list.
"""

import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import stat
import struct


class InvalidImage(ValueError):
    pass


class PEReader:
    def __init__(self, data):
        self.data = data
        if self.read(0, 2) != b'MZ':
            raise InvalidImage('missing DOS signature')
        header = self.number(0x3c, 'I')
        if self.read(header, 4) != b'PE\0\0':
            raise InvalidImage('missing PE signature')
        self.machine, sections = self.unpack(header + 4, 'HH')
        if not 1 <= sections <= 96:
            raise InvalidImage('invalid section count')
        optional_size = self.number(header + 20, 'H')
        optional = header + 24
        self.read(optional, optional_size)
        magic = self.number(optional, 'H')
        if magic == 0x20b:
            self.bits, directory_start, count_offset = 64, 112, 108
            self.image_base = self.number(optional + 24, 'Q')
        elif magic == 0x10b:
            self.bits, directory_start, count_offset = 32, 96, 92
            self.image_base = self.number(optional + 28, 'I')
        else:
            raise InvalidImage('unsupported optional header')
        if optional_size < directory_start:
            raise InvalidImage('truncated optional header')
        self.header_size = self.number(optional + 60, 'I')
        self.read(0, self.header_size)
        count = self.number(optional + count_offset, 'I')
        if count > (optional_size - directory_start) // 8:
            raise InvalidImage('truncated directory array')
        self.directories = [self.unpack(optional + directory_start + i * 8, 'II')
                            for i in range(count)]
        self.sections = []
        for i in range(sections):
            entry = optional + optional_size + i * 40
            self.read(entry, 40)
            virtual_size, rva, size, offset = self.unpack(entry + 8, 'IIII')
            self.read(offset, size)
            self.sections.append((rva, virtual_size, size, offset))

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise InvalidImage('read outside image')
        return self.data[offset:offset + size]

    def unpack(self, offset, fmt):
        return struct.unpack('<' + fmt, self.read(offset, struct.calcsize('<' + fmt)))

    def number(self, offset, fmt):
        return self.unpack(offset, fmt)[0]

    def mapping(self, rva):
        candidates = []
        if 0 <= rva < self.header_size:
            candidates.append((rva, self.header_size - rva))
        for start, virtual_size, size, offset in self.sections:
            if start <= rva < start + max(virtual_size, size):
                delta = rva - start
                if delta >= size:
                    raise InvalidImage('RVA points into unbacked virtual data')
                candidates.append((offset + delta, size - delta))
        if len(candidates) != 1:
            raise InvalidImage('unmapped or ambiguous RVA')
        return candidates[0]

    def at(self, rva, size):
        offset, available = self.mapping(rva)
        if size > available:
            raise InvalidImage('RVA read crosses mapped region')
        return self.read(offset, size)

    def string(self, rva):
        offset, available = self.mapping(rva)
        raw = self.read(offset, min(available, 4096))
        end = raw.find(b'\0')
        if end <= 0:
            raise InvalidImage('empty or unterminated import name')
        try:
            return raw[:end].decode('ascii')
        except UnicodeDecodeError as error:
            raise InvalidImage('non-ASCII import name') from error

    def symbols(self, rva, *, uses_va=False):
        if not rva:
            raise InvalidImage('missing import lookup table')
        width = self.bits // 8
        flag = 1 << (self.bits - 1)
        output = []
        for i in range(65536):
            value = int.from_bytes(self.at(rva + i * width, width), 'little')
            if not value:
                return output
            if value & flag:
                if value & ~(flag | 0xffff):
                    raise InvalidImage('invalid ordinal import')
                output.append({'ordinal': value & 0xffff})
            else:
                name_rva = value - self.image_base if uses_va else value
                self.at(name_rva, 2)  # IMAGE_IMPORT_BY_NAME hint
                output.append({'name': self.string(name_rva + 2)})
        raise InvalidImage('unterminated or excessive import lookup table')

    def imports(self, index, *, delay=False):
        rva, size = self.directories[index] if index < len(self.directories) else (0, 0)
        if (rva, size) == (0, 0):
            return []
        width = 32 if delay else 20
        if not rva or size < width or size // width > 4096:
            raise InvalidImage('invalid import directory')
        output = []
        for i in range(size // width):
            fields = struct.unpack('<' + ('8I' if delay else '5I'), self.at(rva + i * width, width))
            if not any(fields):
                return output
            if delay:
                attrs, name, _, iat, lookup, _, _, _ = fields
                if attrs not in (0, 1):
                    raise InvalidImage('unsupported delay attributes')
                uses_va = attrs == 0
                if uses_va:
                    name -= self.image_base
                    lookup = (lookup or iat) - self.image_base
                else:
                    lookup = lookup or iat
            else:
                lookup, _, _, name, iat = fields
                lookup, uses_va = lookup or iat, False
            output.append({'dll': self.string(name),
                           'symbols': self.symbols(lookup, uses_va=uses_va)})
        raise InvalidImage('unterminated import directory')

    def report(self):
        return {'machine': hex(self.machine), 'bits': self.bits,
                'imports': self.imports(1), 'delay_imports': self.imports(13, delay=True),
                'runtime_resolution': 'NOT_RUN'}


def inspect_file(path):
    """Read a regular image and refuse a concurrent change during inspection."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1024 ** 3:
            raise InvalidImage('expected a nonempty regular image below 1 GiB')
        with mmap.mmap(fd, 0, access=mmap.ACCESS_READ) as data:
            report = PEReader(data).report()
            report['sha256'] = hashlib.sha256(data).hexdigest()
        after = os.fstat(fd)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if stamp(before) != stamp(after) or stamp(after) != stamp(os.stat(path, follow_symlinks=False)):
            raise InvalidImage('image changed during inspection')
        report['bytes'] = before.st_size
        return report
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_file(args.image), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
