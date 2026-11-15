"""Read-only source inventory. Never extracts or executes downloaded content.

Run: python3 -m src.catalog.inventory --config .local/paths.json --output .local/r01
Raw paths and archive entry names stay in the ignored output directory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

ARCHIVES = {".zip", ".rar", ".7z"}
MAX_DECODED_BYTES = 64 * 1024**3


def stamp(path: Path) -> tuple:
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_entry(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not p.is_absolute() and ".." not in p.parts and not any(":" in x for x in p.parts)


def decoded_entries(path: Path) -> list[dict]:
    """Read every regular entry, retaining metadata only; decoder failures propagate."""
    entries = []
    total = 0
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for e in z.infolist():
                if e.is_dir():
                    continue
                h = hashlib.sha256()
                size = 0
                with z.open(e) as f:
                    for chunk in iter(lambda: f.read(1024**2), b""):
                        size += len(chunk)
                        total += len(chunk)
                        if total > MAX_DECODED_BYTES:
                            raise ValueError("DECODE_LIMIT_EXCEEDED")
                        h.update(chunk)
                entries.append({"path": e.filename, "size": size, "sha256": h.hexdigest(),
                                "safe_path": safe_entry(e.filename),
                                "regular": stat.S_IFMT(e.external_attr >> 16) in (0, stat.S_IFREG)})
        return entries
    library = ctypes.util.find_library("archive")
    if not library:
        raise RuntimeError("LIBARCHIVE_UNAVAILABLE")
    lib = ctypes.CDLL(library)
    lib.archive_read_new.restype = ctypes.c_void_p
    for name in ("archive_read_support_format_all", "archive_read_support_filter_all", "archive_read_free"):
        getattr(lib, name).argtypes = [ctypes.c_void_p]
    lib.archive_read_open_filename.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.archive_read_next_header.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.archive_entry_pathname.argtypes = [ctypes.c_void_p]
    lib.archive_entry_pathname.restype = ctypes.c_char_p
    lib.archive_entry_filetype.argtypes = [ctypes.c_void_p]
    lib.archive_entry_filetype.restype = ctypes.c_uint
    lib.archive_read_data.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.archive_read_data.restype = ctypes.c_ssize_t
    lib.archive_error_string.argtypes = [ctypes.c_void_p]
    lib.archive_error_string.restype = ctypes.c_char_p
    handle = lib.archive_read_new()

    def check(result):
        if result < 0:
            error = lib.archive_error_string(handle)
            raise RuntimeError(error.decode(errors="replace") if error else "ARCHIVE_DECODER_ERROR")
        return result

    try:
        check(lib.archive_read_support_format_all(handle))
        check(lib.archive_read_support_filter_all(handle))
        check(lib.archive_read_open_filename(handle, os.fsencode(path), 65536))
        entry = ctypes.c_void_p()
        buffer = ctypes.create_string_buffer(1024**2)
        while True:
            result = check(lib.archive_read_next_header(handle, ctypes.byref(entry)))
            if result == 1:
                break
            raw_name = lib.archive_entry_pathname(entry)
            name = raw_name.decode(errors="replace") if raw_name else ""
            kind = lib.archive_entry_filetype(entry)
            if kind == stat.S_IFDIR:
                continue
            h = hashlib.sha256()
            size = 0
            while True:
                n = check(lib.archive_read_data(handle, buffer, len(buffer)))
                if n == 0:
                    break
                size += n
                total += n
                if total > MAX_DECODED_BYTES:
                    raise ValueError("DECODE_LIMIT_EXCEEDED")
                h.update(buffer.raw[:n])
            entries.append({"path": name, "size": size, "sha256": h.hexdigest(),
                            "safe_path": safe_entry(name), "regular": kind == stat.S_IFREG})
    finally:
        lib.archive_read_free(handle)
    return entries


def inspect_file(path: Path, decode: bool = True) -> dict:
    before = stamp(path)
    result = {"path": str(path), "size": before[2], "stamp": before,
              "sha256": sha256(path), "integrity": "NOT_APPLICABLE"}
    if path.suffix.lower() == ".crdownload":
        result["integrity"] = "INCOMPLETE"
    elif path.suffix.lower() in ARCHIVES and decode:
        try:
            result["entries"] = decoded_entries(path)
            unsafe = any(not e["safe_path"] or not e["regular"] for e in result["entries"])
            names = [e["path"].replace("\\", "/") for e in result["entries"]]
            duplicate_names = len(names) != len(set(names))
            result["integrity"] = "UNSAFE_ENTRIES" if unsafe or duplicate_names else "DECODED"
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as e:
            result["integrity"] = "DECODE_FAILED"
            result["error"] = str(e)
    elif path.suffix.lower() == ".exe":
        with path.open("rb") as f:
            result["pe_header"] = f.read(2) == b"MZ"
        result["integrity"] = "EXECUTABLE_NOT_RUN"
    if stamp(path) != before:
        result["integrity"] = "SOURCE_CHANGED"
    return result


def history(workspace: Path) -> tuple[dict, dict]:
    known = {}
    other = {}
    for filename, key in (("remaining-packages.json", "packages"), ("state.json", "archives"), ("tool-packages.json", None)):
        p = workspace / filename
        data = json.loads(p.read_text())
        rows = data[key] if key else data
        for row in rows:
            source = row.get("source")
            if source:
                known[source] = {"history": filename, "id": row["id"],
                                 "sha256": row.get("sha256", row.get("source_sha256")),
                                 "kind": "tool" if filename == "tool-packages.json" else "stellar_mod"}
        if filename == "remaining-packages.json":
            other = {x["source"]: x["reason"] for x in data["other_games"]}
    return known, other


def classify(row: dict, known: dict, other: dict) -> str:
    """One scope attribution, independent of integrity/duplicate/variant status."""
    p = Path(row["path"])
    if p.suffix.lower() == ".crdownload":
        return "incomplete_download"
    if str(p) in known:
        return known[str(p)]["kind"]
    if str(p) in other:
        return "other_game_historical"
    if p.suffix.lower() not in ARCHIVES | {".exe"}:
        return "unrelated_loose_download"
    names = [x["path"].lower() for x in row.get("entries", [])]
    if any("ue4ss" in n for n in names):
        return "loader_or_runtime_candidate"
    if any(n.endswith((".esp", ".esm", ".ba2", ".bsa")) for n in names):
        return "other_game_content"
    if any(n.endswith((".dekcns.json", ".dekani.json")) or "/sb/" in "/" + n for n in names):
        return "stellar_content_candidate"
    if any(n.endswith((".pak", ".utoc", ".ucas", ".lua")) for n in names):
        return "game_mod_candidate_unconfirmed"
    if p.suffix.lower() == ".exe" or any(n.endswith(".exe") for n in names):
        return "tool_or_runtime_candidate"
    return "unclassified_candidate"


def emit_json(path: Path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def run(config: Path, output: Path, resume: bool = False):
    paths = {k: Path(v).resolve() for k, v in json.loads(config.read_text())["paths"].items()}
    output = output.resolve()
    # An inventory may never write into any source tree (or replace a source root).
    for source in paths.values():
        if output == source or source in output.parents or output in source.parents:
            raise ValueError("Output overlaps a protected source")
    output.mkdir(parents=True, exist_ok=True)
    downloads, game, workspace = (paths[k] for k in ("downloads", "game", "existing_mod_workspace"))
    known, other = history(workspace)
    sources = sorted({p.resolve() for p in downloads.iterdir() if p.is_file()} |
                     {Path(p).resolve() for p in known} |
                     {p.resolve() for p in (workspace / "dependencies").iterdir() if p.suffix.lower() in ARCHIVES | {".exe"}})
    initial = {str(p): stamp(p) if p.exists() else None for p in sources}
    rows = []
    cache = {}
    if resume and (output / "sources.jsonl").exists():
        for line in (output / "sources.jsonl").read_text().splitlines():
            row = json.loads(line)
            cache[row["path"]] = row
    with (output / "sources.jsonl").open("w") as progress:
        for i, p in enumerate(sources, 1):
            if not p.exists():
                row = {"path": str(p), "integrity": "MISSING", "sha256": None}
            else:
                previous = cache.get(str(p))
                # Resume verifies bytes again; metadata alone never proves hash identity.
                fresh = inspect_file(p, decode=False) if previous else None
                if previous and fresh["sha256"] == previous["sha256"] and fresh["integrity"] != "SOURCE_CHANGED":
                    row = dict(previous, stamp=fresh["stamp"])
                elif str(p) in other:
                    row = fresh or inspect_file(p, decode=False)
                    row["integrity"] = "EXCLUDED_OTHER_GAME_NOT_DECODED"
                else:
                    row = inspect_file(p)
            row["id"] = "input-" + hashlib.sha256(os.fsencode(p)).hexdigest()[:20]
            row["attribution"] = classify(row, known, other)
            row["location"] = "downloads" if p.parent == downloads else "external_dependency"
            if str(p) in known:
                row["historical"] = known[str(p)]
                row["historical_hash_matches"] = row["sha256"] == known[str(p)]["sha256"]
            progress.write(json.dumps(row, ensure_ascii=False) + "\n")
            progress.flush()
            rows.append(row)
            if i % 25 == 0 or row["integrity"] in {"DECODE_FAILED", "SOURCE_CHANGED", "MISSING"}:
                print(json.dumps({"processed": i, "total": len(sources), "status": row["integrity"]}), flush=True)
    installed_paths = set()
    for root in [game / "SB/Binaries/Win64/ue4ss", game / "SB/Content/Paks/~mods", game / "SB/Content/Paks/LogicMods"]:
        installed_paths.update(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() not in {".log", ".dmp"})
    installed_paths.add(game / "SB/Binaries/Win64/dwmapi.dll")
    prior = json.loads((workspace / "state.json").read_text())
    original_paths = {Path(x["path"]) for x in prior["original_game_files"]}
    baseline = {str(game / x["path"]): x["sha256"] for x in prior["installed_files"]}
    baseline.update({x["path"]: x["sha256"] for x in prior["original_game_files"]})
    installed_paths.update(Path(p) for p in baseline)
    installed = []
    for p in sorted(installed_paths | original_paths):
        row = inspect_file(p, decode=False) if p.exists() else {"path": str(p), "integrity": "MISSING", "sha256": None}
        row["kind"] = ("protected_original" if p in original_paths else
                       "protected_save_backup" if p.suffix.lower() == ".sav" else "installed_mod_or_loader")
        if str(p) in baseline:
            row["historical_hash_matches"] = row["sha256"] == baseline[str(p)]
        installed.append(row)
    prefix = game.parents[1] / "compatdata/3489700/pfx"
    save_root = prefix / "drive_c/users/steamuser/AppData/Local/SB/Saved/SaveGames"
    saves = [inspect_file(p, decode=False) for p in sorted(save_root.rglob("*")) if p.is_file()]
    config_path = game / "SB/Binaries/Win64/ue4ss/Mods/mods.txt"
    activation = [{"module": m.group(1).strip(), "enabled": m.group(2) == "1"}
                  for line in config_path.read_text().splitlines()
                  if (m := re.fullmatch(r"\s*([^;#]+?)\s*:\s*([01])\s*", line))]
    cns = []
    for p in sorted((game / "SB/Content/Paks/~mods").rglob("*.dekcns.json")):
        value = json.loads(p.read_text(encoding="utf-8-sig"))
        cns.append({"path": str(p), "topology": type(value).__name__,
                    "option_count": len(value) if isinstance(value, list) else None})
    changed = [str(p) for p in sources if (stamp(p) if p.exists() else None) != initial[str(p)]]
    changed.extend(row["path"] for row in installed + saves
                   if "stamp" in row and (not Path(row["path"]).exists() or stamp(Path(row["path"])) != tuple(row["stamp"])))
    current_sources = {str(p.resolve()) for p in downloads.iterdir() if p.is_file()}
    changed.extend(sorted(current_sources - set(initial)))
    groups = defaultdict(list)
    for row in rows:
        if row["sha256"] and row["integrity"] != "INCOMPLETE":
            groups[row["sha256"]].append(row["id"])
    duplicates = [{"sha256": h, "inputs": ids} for h, ids in groups.items() if len(ids) > 1]
    summary = {"sources": len(rows), "downloads": sum(r["location"] == "downloads" for r in rows),
               "external_dependencies": sum(r["location"] != "downloads" for r in rows),
               "attribution": dict(Counter(r["attribution"] for r in rows)),
               "integrity": dict(Counter(r["integrity"] for r in rows)),
               "source_changes": len(changed), "duplicates": len(duplicates),
               "duplicate_aliases": sum(len(g["inputs"]) - 1 for g in duplicates),
               "historical_sources_checked": sum("historical_hash_matches" in r for r in rows),
               "historical_source_mismatches": sum(r.get("historical_hash_matches") is False for r in rows),
               "installed_files": len(installed), "installed_historical_checked": sum("historical_hash_matches" in r for r in installed),
               "installed_kinds": dict(Counter(r["kind"] for r in installed)),
               "installed_historical_mismatches": sum(r.get("historical_hash_matches") is False for r in installed),
               "cns_config_files": len(cns), "protected_save_files": len(saves),
               "runtime_compatibility": "NOT_RUN"}
    manifest = game.parents[1] / "appmanifest_3489700.acf"
    build = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text())
    result = {"schema_version": 1, "created_at_unix": time.time(), "summary": summary,
              "game_build": build.group(1) if build else None,
              "source_changes": changed, "sources": rows, "duplicate_groups": duplicates,
              "installed": installed, "activation": activation, "cns": cns,
              "protected_saves": saves, "save_isolation": "NOT_ESTABLISHED_NO_GAME_LAUNCH"}
    emit_json(output / "inventory.json", result)
    emit_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if changed or any(r["integrity"] in {"MISSING", "SOURCE_CHANGED"} for r in rows + installed + saves):
        raise RuntimeError("INPUT_SNAPSHOT_UNSTABLE_OR_MISSING; inspect private inventory")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="Rehash completed source records and reuse unchanged decoded metadata")
    args = parser.parse_args()
    run(args.config, args.output, args.resume)
