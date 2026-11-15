"""Read-only installed configuration and candidate provenance mapping.

Installed, raw and transformed namespaces remain distinct. Identical bytes are
source candidates, not authorship, write ownership, activation or compatibility.
No function here deploys, restores, launches or changes any configuration.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from src.policy.profiles import EXCLUDED, read_json, unique
from src.policy.static import variant_id

MODS = "SB/Binaries/Win64/ue4ss/Mods"
SETTINGS = "SB/Binaries/Win64/ue4ss/UE4SS-settings.ini"
CORE = {"dwmapi.dll": "SB/Binaries/Win64/dwmapi.dll",
        "ue4ss.dll": "SB/Binaries/Win64/ue4ss/UE4SS.dll"}
SCAN_ROOTS = ["SB/Binaries/Win64/ue4ss", "SB/Content/Paks/~mods", "SB/Content/Paks/LogicMods"]


def key(path):
    value = str(path).replace('\\', '/')
    parts = value.split('/')
    if not value or any(x in {'', '.', '..'} or ':' in x for x in parts):
        raise ValueError("Unsafe relative file target")
    return value.lower()


def fingerprint(path, expected=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Expected regular installed file")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 4 * 1024**2):
            digest.update(chunk)
        stamp = lambda s: [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
        if stamp(before) != stamp(os.fstat(fd)) or stamp(before) != stamp(os.lstat(path)):
            raise ValueError("Installed file changed while reading")
        result = {"size": before.st_size, "sha256": digest.hexdigest(), "stamp": stamp(before)}
        if expected is not None and any(result[k] != expected[k] for k in result):
            raise ValueError("Installed file differs from frozen inventory")
        return result
    finally:
        os.close(fd)


def verify_live(inventory, game, progress=None):
    """Rehash all recorded installed files; refuse extra files in MOD roots."""
    game = game.resolve(strict=True)
    records, normalized = {}, set()
    for row in inventory['installed']:
        path = Path(row['path'])
        relative = path.relative_to(game).as_posix()
        normalized_key = key(relative)
        if normalized_key in normalized or path.resolve(strict=True) != path:
            raise ValueError("Ambiguous or linked installed target")
        normalized.add(normalized_key)
        records[relative] = dict(row, relative=relative, **fingerprint(path, row))
        if progress and len(records) % 100 == 0:
            progress({"installed_files_hashed": len(records), "required": len(inventory['installed'])})
    observed = set()
    for relative_root in SCAN_ROOTS:
        root = game / relative_root
        if not root.exists():
            if any(p.startswith(relative_root + '/') for p in records):
                raise ValueError("Recorded MOD root missing")
            continue
        for parent, directories, files in os.walk(root, followlinks=False):
            if Path(parent).is_symlink() or any((Path(parent) / d).is_symlink() for d in directories):
                raise ValueError("Linked MOD directory not supported")
            for name in files:
                path = Path(parent) / name
                relative = path.relative_to(game).as_posix()
                if relative in records or path.suffix.lower() not in {'.log', '.dmp'}:
                    if path.is_symlink():
                        raise ValueError("Linked MOD file not supported")
                    observed.add(relative)
    expected = {p for p in records if any(p.startswith(r + '/') for r in SCAN_ROOTS)}
    if observed != expected:
        raise ValueError("MOD namespace has unrecorded or missing files; refresh exact affected inventory")
    # Check the read set again after the complete hash traversal.
    for relative, row in records.items():
        s = (game / relative).lstat()
        if row['stamp'] != [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]:
            raise ValueError("Installed namespace changed during snapshot")
    return list(records.values())


def parse_activation(text):
    directives, seen = [], set()
    for number, line in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith((';', '#')):
            continue
        match = re.fullmatch(r'\s*([^:]+?)\s*:\s*([01])\s*(?:;.*)?', line)
        if not match:
            raise ValueError(f"Unparsed activation line {number}")
        module = match[1].strip()
        if '/' in module or '\\' in module or module in {'.', '..'} or any(ord(c) < 32 for c in module):
            raise ValueError("Invalid activation module")
        if module.lower() in seen:
            raise ValueError("Duplicate case-insensitive activation module")
        seen.add(module.lower())
        directives.append({"module": module, "enabled": match[2] == '1', "line": number})
    return directives


def activation_evidence(files, directives):
    modules = defaultdict(lambda: {"files": [], "entrypoints": [], "markers": []})
    for row in files:
        parts = row['relative'].split('/')
        prefix = MODS.split('/')
        if parts[:len(prefix)] != prefix or len(parts) <= len(prefix) + 1:
            continue
        module, tail = parts[len(prefix)], '/'.join(parts[len(prefix) + 1:]).lower()
        slot = modules[module.lower()]
        slot['files'].append(row['relative'])
        if tail in {'scripts/main.lua', 'dlls/main.dll'}:
            slot['entrypoints'].append(row['relative'])
        if tail == 'enabled.txt':
            slot['markers'].append(row['relative'])
    for directive in directives:
        modules[directive['module'].lower()]['directive'] = directive
    result = []
    for name, slot in sorted(modules.items()):
        directive = slot.get('directive')
        intended = directive['enabled'] if directive else None
        condition = ('DISABLED_DECLARATION_WITH_ENABLE_MARKER' if intended is False and slot['markers'] else
                     'ENABLED_DECLARATION_MISSING_ENTRYPOINT' if intended is True and not slot['entrypoints'] else
                     'MARKER_WITHOUT_DECLARATION' if directive is None and slot['markers'] else
                     'UNLISTED_FILES' if directive is None else 'DECLARATION_AND_FILES_RECORDED')
        result.append({"module_key": name, **slot, "declared_enabled": intended,
                       "condition": condition, "effective_activation": "NOT_VERIFIED"})
    return result


def loader_paths(text, files, game_name):
    overrides = []
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((';', '#')):
            continue
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1].lower()
        elif '=' in stripped and section == 'overrides':
            name, value = stripped.split('=', 1)
            if name.strip().lower() in {'modsfolderpath', 'modsfolderpaths', '+modsfolderpaths',
                                        '-modsfolderpaths', 'controllingmodstxt'}:
                overrides.append({'key': name.strip(), 'value': value.split(';', 1)[0].strip()})
    prefix = 'SB/Binaries/Win64/ue4ss/' + game_name + '/'
    specific = [r['relative'] for r in files if r['relative'].startswith(prefix)]
    return {'overrides': overrides, 'game_specific_files': specific,
            'namespace': 'UNMAPPED_OVERRIDE_OR_GAME_SPECIFIC_ROOT' if specific or
                         any(x['value'] for x in overrides) else 'DEFAULT_ROOT_DECLARATION_ONLY',
            'effective_loader_root': 'NOT_RUNTIME_VERIFIED'}


def source_index(inventory, catalog):
    rows = {r['path']: r for r in inventory['sources']}
    index = defaultdict(list)
    for record in catalog['records']:
        if record['kind'] in EXCLUDED:
            continue
        source = rows[record['canonical_path']]
        if source['sha256'] != record['content_sha256']:
            raise ValueError("Catalog source hash binding changed")
        entries = source.get('entries', [])
        if entries and source['integrity'] != 'DECODED':
            continue
        if not entries:
            entries = [{"path": Path(source['path']).name, "size": source['size'], "sha256": source['sha256']}]
        for entry in entries:
            index[(entry['sha256'], entry['size'])].append({"package": record['id'],
                "entry": entry['path'], "archive_sha256": source['sha256']})
    return index


def map_installed(files, inventory, catalog, historical):
    """Associate bytes or hash-bound historical folder claims, never ownership."""
    index = source_index(inventory, catalog)
    by_digest = defaultdict(list)
    for r in catalog['records']:
        if r['kind'] not in EXCLUDED:
            by_digest[r['content_sha256']].append(r['id'])
    folders = []
    for row in historical.get('packages', []):
        candidates = by_digest.get(row.get('source_sha256'), [])
        for folder in row.get('direct_cns_folders', []):
            folders.append((Path(folder), candidates, row['package']))
    result = []
    for row in files:
        if row['kind'] != 'installed_mod_or_loader':
            continue
        exact = index.get((row['sha256'], row['size']), [])
        claims = [{"historical_package": old, "source_candidates": candidates,
                   "folder": str(folder), "status": "HISTORICAL_TRANSFORM_BINDING_ONLY"}
                  for folder, candidates, old in folders if folder in Path(row['path']).parents]
        status = ('COMMON_EMPTY_BYTES' if exact and row['size'] == 0 else
                  'EXACT_SOURCE_ENTRY_BYTES' if exact else
                  'HISTORICAL_TRANSFORM_BINDING_ONLY' if claims else 'SOURCE_UNRESOLVED')
        result.append({"relative": row['relative'], "sha256": row['sha256'], "size": row['size'],
                       "exact_source_candidates": exact, "historical_bindings": claims,
                       "provenance": status, "write_ownership": "UNASSIGNED",
                       "runtime_compatibility": "NOT_RUN"})
    return result


def map_containers(files, catalog, resources):
    raw = defaultdict(list)
    for record in catalog['records']:
        if record['kind'] not in EXCLUDED:
            for container in record['components']['containers']:
                signature = tuple(sorted(container['files'].items()))
                raw[signature].append(variant_id(record, container['stem']))
    groups = defaultdict(dict)
    metadata = {r['id']: r for r in resources['installed']}
    for row in files:
        path = Path(row['relative'])
        if row['kind'] == 'installed_mod_or_loader' and path.suffix.lower() in {'.pak', '.utoc', '.ucas'}:
            slot = groups[path.with_suffix('').as_posix()]
            if path.suffix.lower() in slot:
                raise ValueError('Ambiguous installed container part')
            slot[path.suffix.lower()] = row
    result = []
    for stem, parts in sorted(groups.items()):
        identifiers = None
        if '.utoc' in parts:
            row = metadata.get(parts['.utoc']['path'])
            if row is None or row['toc_sha256'] != parts['.utoc']['sha256']:
                raise ValueError('Installed resource metadata not bound to current TOC bytes')
            identifiers = {'container_id': row['metadata']['container_id'],
                           'chunk_count': len(row['metadata']['chunks']),
                           'hash_scope': row['metadata']['hash_scope']}
        signature = tuple(sorted((suffix, row['sha256']) for suffix, row in parts.items()))
        result.append({'installed_stem': stem,
                       'files': {suffix: {'path': row['path'], 'sha256': row['sha256']} for suffix, row in parts.items()},
                       'exact_raw_component_candidates': raw.get(signature, []),
                       'identifiers': identifiers, 'namespace': 'INSTALLED; never raw-selection aliases',
                       'runtime_compatibility': 'NOT_RUN', 'can_apply': False})
    return result


def preview_loader(files, inventory, catalog, package):
    records = unique(catalog['records'], 'package')
    record = records[package]
    if record['kind'] != 'loader_bundle':
        raise ValueError("Expected a raw loader bundle")
    source = next(r for r in inventory['sources'] if r['path'] == record['canonical_path'])
    if source['sha256'] != record['content_sha256'] or source['integrity'] != 'DECODED':
        raise ValueError("Unverified loader source metadata")
    installed = {key(r['relative']): r for r in files}
    changes = []
    for name, target in CORE.items():
        candidates = [e for e in source['entries'] if PurePosixPath(e['path'].replace('\\', '/')).name.lower() == name]
        if len(candidates) != 1:
            raise ValueError("Missing or ambiguous core loader DLL")
        current, candidate = installed.get(key(target)), candidates[0]
        if current is None or current['kind'] != 'installed_mod_or_loader':
            raise ValueError("Core target missing or protected; no replacement plan")
        changes.append({"target": current['relative'], "current_sha256": current['sha256'],
                        "candidate_entry": candidate['path'], "candidate_sha256": candidate['sha256'],
                        "candidate_size": candidate['size'],
                        "impact": 'KEEP_SAME_BYTES' if (current['sha256'], current['size']) ==
                                  (candidate['sha256'], candidate['size']) else 'REPLACE_CORE_DLL'})
    return {"package": package, "source_sha256": record['content_sha256'], "core_changes": changes,
            "other_installed_files": 'PRESERVE; bundled scripts/settings/content not implicitly selected',
            "can_apply": False, "application": 'EXIT_GAME_REQUIRED',
            "blockers": ['Loader ABI/modules/whole configuration runtime compatibility unverified',
                         'Deployment write ownership and exact transaction/rollback not established',
                         'Legitimate game/local/Cloud save isolation not established']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['inventory', 'catalog', 'resources', 'history', 'game', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    private = Path(__file__).resolve().parents[2] / '.local'
    output = args.output.absolute()
    if private not in output.parents or any(p.is_symlink() for p in [output, *output.parents]):
        raise ValueError("Private owned output required")
    if any(p.stat().st_uid != os.getuid() for p in output.parents if p == private or private in p.parents):
        raise ValueError("Unowned output directory")
    inputs = {name: read_json(getattr(args, name)) for name in ['inventory', 'catalog', 'resources', 'history']}
    inventory, catalog, resources, history = [inputs[name][0] for name in ['inventory', 'catalog', 'resources', 'history']]
    if (catalog['inventory_sha256'] != inputs['inventory'][1] or
            resources['catalog_sha256'] != inputs['catalog'][1] or
            resources['inventory_sha256'] != inputs['inventory'][1]):
        raise ValueError("Catalog and inventory do not share an input snapshot")
    files = verify_live(inventory, args.game, lambda x: print(json.dumps(x), flush=True))
    # The referenced configuration bytes were hashed above; recheck their identity
    # around text reads before describing declarations.
    mods_path = args.game / MODS / 'mods.txt'
    expected = next(r for r in files if r['relative'] == MODS + '/mods.txt')
    directives = parse_activation(mods_path.read_text(encoding='utf-8-sig'))
    fingerprint(mods_path, expected)
    normalized = [{k: d[k] for k in ['module', 'enabled']} for d in directives]
    if normalized != inventory['activation']:
        raise ValueError("Activation declarations differ from frozen inventory")
    settings_expected = next(r for r in files if r['relative'] == SETTINGS)
    settings = (args.game / SETTINGS).read_text(encoding='utf-8-sig')
    fingerprint(args.game / SETTINGS, settings_expected)
    result = {'files': files, 'activation': activation_evidence(files, directives),
              'loader_paths': loader_paths(settings, files, args.game.name),
              'containers': map_containers(files, catalog, resources),
              'provenance': map_installed(files, inventory, catalog, history),
              'loader_previews': [preview_loader(files, inventory, catalog, r['id'])
                                  for r in catalog['records'] if r['kind'] == 'loader_bundle'],
              'input_sha256': {k: v[1] for k, v in inputs.items()},
              'runtime_compatibility': 'NOT_RUN', 'can_apply': False,
              'scope': 'INSTALLED_BYTE_AND_DECLARATION_SNAPSHOT; read-only, no deployment or restore'}
    result['summary'] = {'installed_files_rehashed': len(files),
                         'installed_file_kinds': dict(Counter(r['kind'] for r in files)),
                         'source_mapping': dict(Counter(r['provenance'] for r in result['provenance'])),
                         'activation_directives': len(directives),
                         'declared_enabled': sum(d['enabled'] for d in directives),
                         'enable_markers': sum(len(m['markers']) for m in result['activation']),
                         'installed_mod_containers': len(result['containers']),
                         'containers_with_exact_raw_bytes': sum(bool(r['exact_raw_component_candidates']) for r in result['containers']),
                         'loader_previews': len(result['loader_previews']), 'can_apply': False}
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps(result['summary']), flush=True)


if __name__ == '__main__':
    main()
