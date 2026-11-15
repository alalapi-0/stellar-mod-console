import copy
import hashlib
import tempfile
from pathlib import Path
import unittest

from src.policy.installed import (CORE, MODS, activation_evidence, fingerprint,
                                  loader_paths, map_containers, map_installed, parse_activation,
                                  preview_loader, verify_live)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class InstalledTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.game = Path(self.directory.name)

    def file(self, relative, data=b'own fixture', kind='installed_mod_or_loader'):
        path = self.game / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'path': str(path), 'relative': relative, 'kind': kind, **fingerprint(path)}

    def test_live_snapshot_rehashes_and_refuses_changed_missing_or_added_files(self):
        row = self.file(MODS + '/Own/Scripts/main.lua')
        inventory = {'installed': [row]}
        self.assertEqual(verify_live(inventory, self.game)[0]['sha256'], row['sha256'])
        path = Path(row['path']); path.write_bytes(b'changed fixture')
        with self.assertRaises(ValueError):
            verify_live(inventory, self.game)
        row = self.file(MODS + '/Own/Scripts/main.lua')
        self.file(MODS + '/Unexpected/enabled.txt', b'')
        with self.assertRaises(ValueError):
            verify_live({'installed': [row]}, self.game)
        Path(row['path']).unlink()
        with self.assertRaises(FileNotFoundError):
            fingerprint(row['path'], row)

    def test_linked_file_directory_and_case_collisions_refuse(self):
        row = self.file(MODS + '/Own/Scripts/main.lua')
        link = self.game / MODS / 'Own' / 'linked.lua'; link.symlink_to(row['path'])
        with self.assertRaises(ValueError):
            verify_live({'installed': [row]}, self.game)
        link.unlink()
        collision = self.file(MODS + '/own/scripts/MAIN.lua')
        with self.assertRaises(ValueError):
            verify_live({'installed': [row, collision]}, self.game)
        Path(collision['path']).unlink()
        link = self.game / MODS / 'Alias'; link.symlink_to(self.game / MODS / 'Own', target_is_directory=True)
        with self.assertRaises(ValueError):
            verify_live({'installed': [row]}, self.game)

    def test_recorded_logs_are_preserved_but_new_transient_logs_are_not_managed(self):
        row = self.file(MODS + '/Tool/Saved/Logs/old.log', b'packaged log fixture')
        self.file(MODS + '/Tool/Saved/Logs/current.log', b'transient log fixture')
        result = verify_live({'installed': [row]}, self.game)
        self.assertEqual([r['relative'] for r in result], [row['relative']])

    def test_activation_retains_order_disables_and_both_entry_routes(self):
        rows = [self.file(MODS + '/Old/Scripts/main.lua'),
                self.file(MODS + '/Old/enabled.txt', b''),
                self.file(MODS + '/Native/dlls/main.dll')]
        text = '\ufeff;comment\nOld : 0\nNative : 1 ; explain\nMissing : 1\n'
        directives = parse_activation(text)
        self.assertEqual([d['module'] for d in directives], ['Old', 'Native', 'Missing'])
        evidence = {e['module_key']: e for e in activation_evidence(rows, directives)}
        self.assertEqual(evidence['old']['condition'], 'DISABLED_DECLARATION_WITH_ENABLE_MARKER')
        self.assertEqual(evidence['missing']['condition'], 'ENABLED_DECLARATION_MISSING_ENTRYPOINT')
        self.assertEqual(len(evidence['native']['entrypoints']), 1)
        self.assertTrue(all(e['effective_activation'] == 'NOT_VERIFIED' for e in evidence.values()))

    def test_malformed_duplicate_or_path_activation_is_not_silently_ignored(self):
        for text in ['Bad : 2', 'Bad : 1\nbad : 0', '../Bad : 1', 'Folder\\Bad : 1', 'Bad stuff']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_activation(text)

    def test_external_loader_root_and_game_specific_settings_remain_unmapped(self):
        default = loader_paths('[Overrides]\nModsFolderPath =\n', [], 'Game')
        self.assertEqual(default['namespace'], 'DEFAULT_ROOT_DECLARATION_ONLY')
        external = loader_paths('[Overrides]\nModsFolderPath = /some/other/Mods\n', [], 'Game')
        self.assertEqual(external['namespace'], 'UNMAPPED_OVERRIDE_OR_GAME_SPECIFIC_ROOT')
        specific = loader_paths('', [{'relative': 'SB/Binaries/Win64/ue4ss/Game/Mods/Own/main.lua'}], 'Game')
        self.assertEqual(specific['namespace'], 'UNMAPPED_OVERRIDE_OR_GAME_SPECIFIC_ROOT')

    def fixtures(self):
        contents = {'proxy': b'proxy bytes', 'loader': b'new loader bytes', 'script': b'own script'}
        entries = [{'path': 'bundle/' + name, 'size': len(value), 'sha256': digest(value)}
                   for name, value in contents.items()]
        entries += [{'path': 'bundle/enabled.txt', 'size': 0, 'sha256': digest(b'')}]
        entries[0]['path'] = 'bundle/dwmapi.dll'; entries[1]['path'] = 'bundle/ue4ss/UE4SS.dll'
        inventory = {'sources': [{'path': '/synthetic/archive.zip', 'sha256': 'archive-digest',
                                  'size': 999, 'integrity': 'DECODED', 'entries': entries}]}
        record = {'id': 'bundle', 'canonical_path': '/synthetic/archive.zip',
                  'kind': 'loader_bundle', 'content_sha256': 'archive-digest'}
        return contents, inventory, {'records': [record]}

    def test_exact_bytes_keep_all_source_candidates_and_empty_is_not_authorship(self):
        values, inventory, catalog = self.fixtures()
        catalog['records'].append(dict(catalog['records'][0], id='alternate'))
        rows = [self.file(MODS + '/Own/Scripts/renamed.lua', values['script']),
                self.file(MODS + '/Own/enabled.txt', b''),
                self.file('protected-original.bin', values['script'], 'protected_original'),
                self.file('protected.sav', values['script'], 'protected_save_backup')]
        before = copy.deepcopy((rows, inventory, catalog))
        result = map_installed(rows, inventory, catalog, {})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['provenance'], 'EXACT_SOURCE_ENTRY_BYTES')
        self.assertEqual(len(result[0]['exact_source_candidates']), 2)
        self.assertEqual(result[1]['provenance'], 'COMMON_EMPTY_BYTES')
        self.assertTrue(all(x['write_ownership'] == 'UNASSIGNED' for x in result))
        self.assertEqual((rows, inventory, catalog), before)

    def test_transformed_folder_binding_is_not_exact_byte_or_runtime_proof(self):
        _, inventory, catalog = self.fixtures()
        folder = self.game / 'SB/Content/Paks/~mods/Converted'
        row = self.file(folder.relative_to(self.game).as_posix() + '/new.utoc', b'changed namespace')
        history = {'packages': [{'package': 'old-id', 'source_sha256': 'archive-digest',
                                 'direct_cns_folders': [str(folder)]}]}
        result = map_installed([row], inventory, catalog, history)[0]
        self.assertEqual(result['provenance'], 'HISTORICAL_TRANSFORM_BINDING_ONLY')
        self.assertEqual(result['exact_source_candidates'], [])
        self.assertEqual(result['historical_bindings'][0]['source_candidates'], ['bundle'])
        self.assertEqual(result['runtime_compatibility'], 'NOT_RUN')

    def test_loader_preview_changes_only_core_files_without_touching_real_bytes(self):
        values, inventory, catalog = self.fixtures()
        rows = [self.file(CORE['dwmapi.dll'], values['proxy']),
                self.file(CORE['ue4ss.dll'], b'old loader bytes'), self.file(MODS + '/Own/Scripts/main.lua')]
        result = preview_loader(rows, inventory, catalog, 'bundle')
        self.assertEqual([x['impact'] for x in result['core_changes']], ['KEEP_SAME_BYTES', 'REPLACE_CORE_DLL'])
        self.assertFalse(result['can_apply'])
        self.assertEqual(Path(rows[1]['path']).read_bytes(), b'old loader bytes')
        rows[1]['kind'] = 'protected_original'
        with self.assertRaises(ValueError):
            preview_loader(rows, inventory, catalog, 'bundle')
        rows[1]['kind'] = 'installed_mod_or_loader'
        inventory['sources'][0]['entries'].append(copy.deepcopy(inventory['sources'][0]['entries'][1]))
        with self.assertRaises(ValueError):
            preview_loader(rows, inventory, catalog, 'bundle')

    def test_container_requires_complete_byte_signature_and_current_toc_binding(self):
        prefix = 'SB/Content/Paks/~mods/Renamed'
        rows = [self.file(prefix + suffix, suffix.encode()) for suffix in ['.pak', '.utoc', '.ucas']]
        parts = {Path(r['relative']).suffix: r['sha256'] for r in rows}
        catalog = {'records': [{'id': 'source', 'kind': 'container_mod', 'components': {'containers':
                                [{'stem': 'original-name', 'files': parts}]}}]}
        toc = rows[1]
        resources = {'installed': [{'id': toc['path'], 'toc_sha256': toc['sha256'],
                                    'metadata': {'container_id': 'abc', 'chunks': [], 'hash_scope': 'STORED_ONLY'}}]}
        exact = map_containers(rows, catalog, resources)[0]
        self.assertEqual(len(exact['exact_raw_component_candidates']), 1)
        changed = copy.deepcopy(rows); changed[2]['sha256'] = 'different-namespace'
        self.assertEqual(map_containers(changed, catalog, resources)[0]['exact_raw_component_candidates'], [])
        resources['installed'][0]['toc_sha256'] = 'stale'
        with self.assertRaises(ValueError):
            map_containers(rows, catalog, resources)


if __name__ == '__main__':
    unittest.main()
