"""Read-only IoStore resource metadata; stored hashes are not payload verification."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct
import zipfile

from src.catalog.inventory import emit_json, sha256, stamp
from src.policy.static import archive_metadata, toc_identifiers, variant_id

NONE = 0xffffffff
OLD_TYPES = ("Invalid", "InstallManifest", "ExportBundleData", "BulkData", "OptionalBulkData",
             "MemoryMappedBulkData", "LoaderGlobalMeta", "LoaderInitialLoadMeta", "LoaderGlobalNames",
             "LoaderGlobalNameHashes", "ContainerHeader", "ShaderCodeLibrary", "ShaderCode")


class Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        if n < 0 or n > len(self.data) - self.pos:
            raise ValueError("Truncated IoStore metadata")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def number(self, fmt="I") -> int:
        return struct.unpack("<" + fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        count = self.number("i")
        data = self.take(abs(count) * (2 if count < 0 else 1))
        value = data.decode("utf-16-le" if count < 0 else "utf-8")
        if value and (not value.endswith("\0") or "\0" in value[:-1]):
            raise ValueError("Invalid FString terminator")
        return value[:-1] if value else ""

    def array(self, width: int, fmt: str) -> list[tuple]:
        count = self.number()
        if count > (len(self.data) - self.pos) // width:
            raise ValueError("Truncated directory array")
        return [struct.unpack("<" + fmt, self.take(width)) for _ in range(count)]


def resource_key(path: str) -> str:
    """Remove mount-relative prefix; retain internal traversal as an error."""
    parts = path.replace("\\", "/").split("/")
    while parts and parts[0] in {"", ".", ".."}:
        parts.pop(0)
    if any(p in {"", ".", ".."} for p in parts):
        raise ValueError("Ambiguous resource path")
    # UE/retoc comparison uses ASCII case folding, not Unicode case folding.
    return "/".join(parts).translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def virtual_resource_key(path: str) -> str | None:
    """Cooked project roots share /Game; Engine and plugin mounts stay distinct."""
    parts = resource_key(path).split("/")
    if len(parts) >= 3 and parts[1] == "content":
        root = "engine" if parts[0] == "engine" else "game"
        return "/" + root + "/" + "/".join(parts[2:])
    if len(parts) >= 5 and parts[1] == "plugins" and "content" in parts[3:]:
        index = parts.index("content", 3)
        return "/" + parts[index - 1] + "/" + "/".join(parts[index + 1:])
    return None


def directory_paths(data: bytes, chunk_count: int) -> tuple[str, dict[int, str]]:
    if not data:
        return "", {}
    r = Reader(data)
    mount = r.string()
    dirs, files = r.array(16, "IIII"), r.array(12, "III")
    string_count = r.number()
    if string_count > (len(data) - r.pos) // 4:
        raise ValueError("Truncated string table")
    strings = [r.string() for _ in range(string_count)]
    if r.pos != len(data):
        raise ValueError("Unexpected directory index trailer")
    visited_dirs, visited_files, paths = set(), set(), {}

    def name(i):
        if i >= len(strings) or not strings[i] or "/" in strings[i] or "\\" in strings[i]:
            raise ValueError("Invalid directory name reference")
        return strings[i]

    pending = [(0, [])] if dirs else []
    while pending:
        index, prefix = pending.pop()
        if index >= len(dirs) or index in visited_dirs:
            raise ValueError("Invalid/cyclic directory reference")
        visited_dirs.add(index)
        n, child, sibling, file = dirs[index]
        prefix = prefix + ([name(n)] if n != NONE else [])
        while file != NONE:
            if file >= len(files) or file in visited_files:
                raise ValueError("Invalid/cyclic file reference")
            visited_files.add(file)
            n, next_file, chunk = files[file]
            if chunk >= chunk_count or chunk in paths:
                raise ValueError("Invalid/duplicate directory chunk reference")
            paths[chunk] = mount + "/".join(prefix + [name(n)])
            file = next_file
        siblings = set()
        while child != NONE:
            if child >= len(dirs) or child in siblings:
                raise ValueError("Invalid/cyclic child reference")
            siblings.add(child)
            pending.append((child, prefix))
            child = dirs[child][2]
    if len(visited_dirs) != len(dirs) or len(visited_files) != len(files):
        raise ValueError("Unreachable directory/file metadata")
    if len({resource_key(p) for p in paths.values()}) != len(paths):
        raise ValueError("Duplicate case-insensitive resource path")
    return mount, paths


def toc_resources(data: bytes) -> dict:
    ident = toc_identifiers(data)
    # Narrow to the layouts actually observed in this frozen input population.
    if ident["toc_version"] not in {2, 3}:
        raise ValueError("Resource metadata layout NOT_SUPPORTED (only v2/v3)")
    count = len(ident["chunk_ids"])
    if len(set(ident["chunk_ids"])) != count:
        raise ValueError("Duplicate chunk identifier within container")
    block_count, block_size, methods, method_width = struct.unpack_from("<IIII", data, 28)
    directory_size = struct.unpack_from("<I", data, 48)[0]
    flags = data[80]
    if flags & ~15 or flags & 2:
        raise ValueError("Unknown/encrypted metadata; NOT_SUPPORTED")
    if block_size != 12:
        raise ValueError("Unsupported compression block layout")
    r = Reader(data)
    r.take(0x90 + count * 12)
    offsets = [r.take(10) for _ in range(count)]
    r.take(block_count * block_size + methods * method_width)
    if flags & 4:
        signature_size = r.number()
        r.take(signature_size * 2 + block_count * 20)
    mount, paths = directory_paths(r.take(directory_size), count)
    chunks = []
    for index, raw_id in enumerate(ident["chunk_ids"]):
        meta = r.take(33)
        kind = bytes.fromhex(raw_id)[11]
        if kind >= len(OLD_TYPES) or meta[32] & ~3:
            raise ValueError("Unknown chunk type/meta flags")
        path = paths.get(index)
        chunks.append({"id": raw_id, "type": OLD_TYPES[kind], "stored_hash": meta[:32].hex(),
                       "size": int.from_bytes(offsets[index][5:], "big"), "path": path,
                       "resource_key": resource_key(path) if path else None})
    if r.pos != len(data):
        raise ValueError("Unexpected TOC trailer; metadata NOT_VERIFIED")
    return {"toc_version": ident["toc_version"], "container_id": ident["container_id"],
            "mount_point": mount, "chunks": chunks, "flags": flags,
            "hash_scope": "STORED_32_BYTE_METADATA_ONLY; UCAS payload NOT_REHASHED"}


def overlaps(containers: list[dict], field: str) -> dict:
    index = defaultdict(list)
    for container in containers:
        for chunk in container["metadata"]["chunks"]:
            if chunk[field]:
                index[chunk[field]].append({"container": container["id"], "chunk": chunk["id"],
                                            "hash": chunk["stored_hash"], "size": chunk["size"], "path": chunk["path"]})
    groups = {}
    for key, rows in index.items():
        if len({r["container"] for r in rows}) < 2:
            continue
        known = all(int(r["hash"], 16) for r in rows)
        status = "SAME_STORED_HASH_AND_SIZE" if len({(r["hash"], r["size"]) for r in rows}) == 1 else "DIFFERING_STORED_METADATA"
        groups[key] = {"status": status if known else "UNKNOWN_ZERO_HASH", "members": rows,
                       "runtime_rule": "UNKNOWN; no deployment or override order inferred"}
    return groups


def build_index(catalog: dict, inventory: dict) -> dict:
    sources = {r["path"]: r for r in inventory["sources"]}
    raw, live, deferred = [], [], []
    for record in catalog["records"]:
        if not record["components"]["containers"] or record["kind"] in {"other_game_historical", "other_game_content"}:
            continue
        source = sources[record["canonical_path"]]
        path = Path(source["path"])
        entries = {e["path"].replace("\\", "/"): e for e in source.get("entries", [])}
        tocs = archive_metadata(path) if path.suffix.lower() != ".zip" else None
        for container in record["components"]["containers"]:
            name = container["stem"] + ".utoc"
            ident = variant_id(record, container["stem"])
            try:
                entry = entries[name]
                if tocs is None:
                    with zipfile.ZipFile(path) as z:
                        data = z.read(entry["path"])
                else:
                    data = tocs[name]
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError("Frozen raw TOC changed")
                raw.append({"id": ident, "package": record["id"], "entry": name,
                            "toc_sha256": entry["sha256"], "metadata": toc_resources(data)})
            except (KeyError, ValueError) as error:
                deferred.append({"namespace": "raw", "id": ident, "reason": str(error)})
    for row in inventory["installed"]:
        if not row["path"].lower().endswith(".utoc"):
            continue
        path = Path(row["path"])
        try:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("Frozen installed TOC changed")
            live.append({"id": row["path"], "role": row["kind"], "toc_sha256": row["sha256"], "metadata": toc_resources(data)})
        except ValueError as error:
            deferred.append({"namespace": "installed", "id": row["path"], "reason": str(error)})
    result = {"schema_version": 1, "raw": raw, "installed": live, "deferred": deferred}
    mods = [r for r in live if r["role"] == "installed_mod_or_loader"]
    for label, containers in (("raw", raw), ("installed", live), ("installed_mods_only", mods)):
        result[label + "_chunk_overlaps"] = overlaps(containers, "id")
        result[label + "_path_overlaps"] = overlaps(containers, "resource_key")
    result["summary"] = {"raw_containers": len(raw), "installed_containers": len(live), "deferrals": len(deferred)}
    result["summary"]["installed_roles"] = dict(Counter(r["role"] for r in live))
    for label in ("raw", "installed", "installed_mods_only"):
        for key in ("chunk", "path"):
            result["summary"][label + "_" + key + "_overlap_statuses"] = dict(Counter(g["status"] for g in result[label + "_" + key + "_overlaps"].values()))
    result["remaining"] = ["Stored hashes have not been verified against decompressed UCAS payload",
                           "Load order, dependency/body/material/physics and runtime compatibility remain unknown",
                           "Writer/hotkey/CNS identities and all unified controller entry points remain unverified"]
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "inventory", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    if Path(__file__).resolve().parents[2] / ".local" not in args.output.resolve().parents:
        raise ValueError("Raw/private metadata must remain in ignored .local")
    inventory = json.loads(args.inventory.read_text())
    rows = inventory["sources"] + inventory["installed"]
    def unchanged():
        for row in rows:
            if stamp(Path(row["path"])) != tuple(row["stamp"]):
                raise ValueError("Frozen input changed; refresh affected inventory")
    unchanged()
    result = build_index(json.loads(args.catalog.read_text()), inventory)
    unchanged()
    result["catalog_sha256"], result["inventory_sha256"] = sha256(args.catalog), sha256(args.inventory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emit_json(args.output, result)
    print(json.dumps(result["summary"]))
