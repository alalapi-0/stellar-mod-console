"""Read-only raw-package conflict discovery; not the runtime controller of R10."""
from __future__ import annotations

import argparse
from collections import defaultdict
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

from src.catalog.inventory import emit_json, sha256, stamp

TOC_MAGIC = b"-==--==--==--==-"


def archive_tocs(path: Path) -> dict[str, bytes]:
    """Read small TOC entries in memory through system libarchive; no extraction."""
    library = ctypes.util.find_library("archive")
    if not library:
        raise RuntimeError("LIBARCHIVE_UNAVAILABLE")
    lib = ctypes.CDLL(library)
    lib.archive_read_new.restype = ctypes.c_void_p
    for name in ("archive_read_support_format_all", "archive_read_support_filter_all", "archive_read_free", "archive_read_data_skip"):
        getattr(lib, name).argtypes = [ctypes.c_void_p]
    lib.archive_read_open_filename.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.archive_read_next_header.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.archive_entry_pathname.argtypes = [ctypes.c_void_p]
    lib.archive_entry_pathname.restype = ctypes.c_char_p
    lib.archive_read_data.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.archive_read_data.restype = ctypes.c_ssize_t
    lib.archive_error_string.argtypes = [ctypes.c_void_p]
    lib.archive_error_string.restype = ctypes.c_char_p
    handle = lib.archive_read_new()

    def check(result):
        if result < 0:
            error = lib.archive_error_string(handle)
            raise RuntimeError(error.decode(errors="replace") if error else "ARCHIVE_READ_ERROR")
        return result

    found = {}
    try:
        check(lib.archive_read_support_format_all(handle))
        check(lib.archive_read_support_filter_all(handle))
        check(lib.archive_read_open_filename(handle, str(path).encode(), 65536))
        entry = ctypes.c_void_p()
        buffer = ctypes.create_string_buffer(1024**2)
        while True:
            if check(lib.archive_read_next_header(handle, ctypes.byref(entry))) == 1:
                break
            raw = lib.archive_entry_pathname(entry)
            name = raw.decode(errors="replace").replace("\\", "/") if raw else ""
            if not name.lower().endswith(".utoc"):
                check(lib.archive_read_data_skip(handle))
                continue
            data = bytearray()
            while n := check(lib.archive_read_data(handle, buffer, len(buffer))):
                if len(data) + n > 128 * 1024**2:
                    raise ValueError("TOC_MEMORY_LIMIT_EXCEEDED; identifier index NOT_RUN")
                data.extend(buffer.raw[:n])
            if name in found:
                raise ValueError("Duplicate TOC entry")
            found[name] = bytes(data)
    finally:
        lib.archive_read_free(handle)
    return found


def toc_identifiers(data: bytes) -> dict:
    """Read the fixed header/chunk-ID prefix, never decompress game resources.

    Layout reference: trumank/retoc v0.1.5, retoc/src/lib.rs.
    Only identifier indexing is performed, not complete TOC/hash verification.
    """
    if len(data) < 0x90 or data[:16] != TOC_MAGIC:
        raise ValueError("Unrecognized or truncated IoStore header")
    version = data[16]
    header_size, count = struct.unpack_from("<II", data, 20)
    if header_size != 0x90 or version not in range(1, 9):
        raise ValueError("Unsupported IoStore layout/version")
    if count > (len(data) - header_size) // 12:
        raise ValueError("Truncated chunk-ID array")
    cid = struct.unpack_from("<Q", data, 56)[0]
    chunks = [data[header_size + i * 12:header_size + (i + 1) * 12].hex() for i in range(count)]
    return {"toc_version": version, "container_id": f"{cid:016x}", "chunk_ids": chunks,
            "scope": "IDENTIFIERS_ONLY_RAW_SOURCE; no resource/hash compatibility proof"}


def variant_id(record: dict, stem: str) -> str:
    return record["id"] + "::" + hashlib.sha256(stem.encode()).hexdigest()[:16]


def build_graph(catalog: dict, inventory: dict) -> dict:
    source_rows = {r["path"]: r for r in inventory["sources"]}
    variants = []
    groups = []
    deferred = []
    explicit = defaultdict(list)
    for record in catalog["records"]:
        if record["kind"] in {"other_game_historical", "other_game_content", "incomplete_download", "unrelated_loose_download"}:
            continue
        row = source_rows[record["canonical_path"]]
        path = Path(row["path"])
        entry_by_name = {e["path"].replace("\\", "/"): e for e in row.get("entries", [])}
        toc_bytes = {}
        archive_error = None
        if path.suffix.lower() != ".zip" and record["components"]["containers"]:
            try:
                toc_bytes = archive_tocs(path)
            except (ValueError, OSError, RuntimeError) as error:
                archive_error = str(error)
        for container in record["components"]["containers"]:
            stem = container["stem"]
            ident = variant_id(record, stem)
            variant = {"id": ident, "package": record["id"], "source_stem": stem,
                       "files": container["files"], "runtime_compatibility": "NOT_RUN"}
            if ".utoc" in container["files"]:
                try:
                    expected = entry_by_name[stem + ".utoc"]
                    if path.suffix.lower() == ".zip":
                        # ZIP can contain Windows separators; use the original name.
                        with zipfile.ZipFile(path) as z:
                            data = z.read(expected["path"])
                    else:
                        if archive_error:
                            raise RuntimeError(archive_error)
                        data = toc_bytes[stem + ".utoc"]
                    if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                        raise ValueError("TOC bytes no longer match frozen input")
                    variant["identifiers"] = toc_identifiers(data)
                except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, KeyError) as error:
                    deferred.append({"variant": ident, "reason": str(error)})
            variants.append(variant)
            filename = path.name
            if re.match(r"HD-ATOOL(?:-k[23])?-1662-6-1-", filename):
                explicit["atool-6.1"].append(ident)
            if filename.startswith("SpeedMasterEve") and record["filename_hints"]["mod_id"] == 2205:
                explicit["speed-1.0.9"].append(ident)
            if filename.startswith("FlyingEve") and record["filename_hints"]["mod_id"] == 2269:
                explicit["flying-1.1.4"].append(ident)
            if record["filename_hints"]["mod_id"] == 1714 and "Keyhole" in filename:
                explicit["keyhole-og-1714"].append(ident)
        if record["kind"] == "loader_bundle":
            ident = record["id"] + "::loader"
            variants.append({"id": ident, "package": record["id"], "component": "UE4SS loader",
                             "runtime_compatibility": "NOT_RUN"})
            explicit["ue4ss-loader"].append(ident)
    reasons = {"atool-6.1": "Three distinct raw variants deploy the same HD-ATOOL_P triple",
               "speed-1.0.9": "Normal/chunk26 deploy one SpeedMasterEve triple and Lua module",
               "flying-1.1.4": "Normal/chunk23 deploy one FlyingEve triple and Lua module",
               "keyhole-og-1714": "Three original replacers target the Keyhole outfit; preserve current Exposed choice",
               "ue4ss-loader": "One UE4SS loader provider per process/profile; bundled mod content may be selected separately"}
    for name, members in sorted(explicit.items()):
        if len(members) > 1:
            groups.append({"id": name, "type": "exclusive_variant", "members": members, "max_enabled": 1,
                           "reason": reasons[name], "scope": "RAW_UNCONVERTED_COMPONENT_SELECTION"})
    cids = defaultdict(list)
    chunks = defaultdict(set)
    for variant in variants:
        meta = variant.get("identifiers")
        if meta:
            cids[meta["container_id"]].append(variant["id"])
            for chunk in meta["chunk_ids"]:
                chunks[(meta["toc_version"], chunk)].add(variant["id"])
    overlaps = {f"v{version}:{chunk}": sorted(ids) for (version, chunk), ids in chunks.items() if len(ids) > 1}
    return {"schema_version": 1, "scope": "RAW_SOURCE_ONLY; converted/live namespaces need a separate exact index",
            "variants": variants, "known_exclusive_groups": groups,
            "container_id_overlaps": {cid: ids for cid, ids in cids.items() if len(ids) > 1},
            "chunk_id_overlaps": overlaps, "deferred": deferred,
            "unknowns": ["Full archive resource paths/material/body/physics/CNS IDs and write ownership incomplete",
                         "Shared chunk/container IDs require payload/path/override analysis; overlap alone is not hard_conflict",
                         "Installed converted namespace and applicable historical 42 overrides remain to verify",
                         "All runtime entry points, hotkey rebinding and compatibility controller NOT_IMPLEMENTED"],
            "summary": {"variants_and_loader_components": len(variants), "exclusive_groups": len(groups),
                        "toc_identifiers_indexed": sum("identifiers" in v for v in variants), "identifier_deferrals": len(deferred),
                        "container_overlap_groups": sum(len(ids) > 1 for ids in cids.values()),
                        "overlapping_chunk_ids": len(overlaps), "runtime_compatibility": "NOT_RUN"}}


def check_known_selection(graph: dict, selected: list[str]) -> dict:
    """Diagnostic only: lack of a known violation never authorizes application."""
    choices = set(selected)
    known = {v["id"] for v in graph["variants"]}
    violations = [{"type": "unknown_selection", "id": i} for i in sorted(choices - known)]
    if len(choices) != len(selected):
        violations.append({"type": "duplicate_selection"})
    for group in graph["known_exclusive_groups"]:
        chosen = choices.intersection(group["members"])
        if len(chosen) > group["max_enabled"]:
            violations.append({"type": "exclusive_variant", "group": group["id"], "selected": sorted(chosen)})
    return {"violations": violations, "status": "REJECTED_STATIC" if violations else "NO_KNOWN_STATIC_VIOLATION",
            "runtime_compatibility": "NOT_VERIFIED", "can_apply": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if Path(__file__).resolve().parents[2] / ".local" not in args.output.resolve().parents:
        raise ValueError("Raw analysis must remain in ignored .local")
    inventory = json.loads(args.inventory.read_text())
    for row in inventory["sources"]:
        if stamp(Path(row["path"])) != tuple(row["stamp"]):
            raise ValueError("Frozen source changed; re-inventory the affected source")
    graph = build_graph(json.loads(args.catalog.read_text()), inventory)
    for row in inventory["sources"]:
        if stamp(Path(row["path"])) != tuple(row["stamp"]):
            raise ValueError("Source changed while indexing; candidate not saved")
    graph["catalog_sha256"] = sha256(args.catalog)
    graph["inventory_sha256"] = sha256(args.inventory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emit_json(args.output, graph)
    print(json.dumps(graph["summary"], ensure_ascii=False))
