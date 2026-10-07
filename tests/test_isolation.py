import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.testing.isolation import (IsolationRefused, command, construct,
                                   owned_ancestors, sources_from_context)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parents[1] / '.local/r04/tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.parent = self.base / 'profiles'
        self.sources = {}
        for name in ['game', 'proton']:
            source = self.base / name
            source.mkdir()
            (source / 'original').write_text('must remain at source')
            self.sources[name] = source
        self.protected = list(self.sources.values())
        self.name = 'a' * 32

    def create(self):
        return construct(self.parent, self.protected, self.sources, self.name)

    def test_fresh_construction_never_copies_protected_content(self):
        root = self.create()
        manifest = json.loads((root / 'manifest.json').read_text())
        self.assertEqual(manifest['construction']['copied_external_files'], 0)
        self.assertEqual(manifest['construction']['files'].keys(), {'protected/marker.txt'})
        self.assertEqual(list((root / 'work/compatdata').iterdir()), [])
        self.assertEqual(list((root / 'work/profile').iterdir()), [])
        with self.assertRaisesRegex(IsolationRefused, 'already exists'):
            self.create()
        self.assertTrue(all((p / 'original').read_text() == 'must remain at source'
                            for p in self.sources.values()))

    def test_overlaps_refused_in_both_directions(self):
        for protected in [self.base, self.parent / self.name / 'save']:
            protected.mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(IsolationRefused, 'overlaps'):
                construct(self.parent, [protected], self.sources, self.name)

    def test_symlink_ancestor_refused_before_creation(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.base / 'game', target_is_directory=True)
        with self.assertRaisesRegex(IsolationRefused, 'symlink'):
            construct(alias / 'nested', self.protected, self.sources, self.name)
        self.assertFalse((self.base / 'game/nested').exists())
        with self.assertRaises(IsolationRefused):
            owned_ancestors(self.base / '..' / 'ambiguous')

    def test_non_owner_refused_before_target_creation(self):
        with patch('src.testing.isolation.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(IsolationRefused, 'not owned'):
                self.create()
        self.assertFalse(self.parent.exists())

    def test_hardlink_and_symlink_private_contents_refused(self):
        root = self.create()
        target = root / 'work/alias'
        target.symlink_to(self.sources['game'] / 'original')
        with self.assertRaisesRegex(IsolationRefused, 'symlink'):
            command(root, self.sources, '{}')
        target.unlink()
        os.link(self.sources['game'] / 'original', target)
        with self.assertRaisesRegex(IsolationRefused, 'shared-inode'):
            command(root, self.sources, '{}')

    def test_closed_command_has_no_host_root_or_runtime_binding(self):
        root = self.create()
        args = command(root, self.sources, '{}')
        for flag in ['--unshare-all', '--unshare-user', '--disable-userns', '--clearenv', '--new-session', '--die-with-parent']:
            self.assertIn(flag, args)
        self.assertNotIn('--share-net', args)
        self.assertEqual([(args[i + 1], args[i + 2]) for i, value in enumerate(args) if value == '--bind'],
                         [(str(root / 'work'), '/work')])
        self.assertEqual(args[-4:], ['/usr/bin/python3', '-B', '/probe.py', '{}'])
        with patch('src.testing.isolation.shutil.which', return_value=None):
            with self.assertRaisesRegex(IsolationRefused, 'no fallback'):
                command(root, self.sources, '{}')
        other = dict(self.sources, game=self.base)
        with self.assertRaisesRegex(IsolationRefused, 'differ'):
            command(root, other, '{}')

    def test_context_rejects_extra_source_and_unresolved_source(self):
        raw = {k: str(p) for k, p in self.sources.items()}
        self.assertEqual(sources_from_context({'readonly_sources': raw}), self.sources)
        with self.assertRaises(IsolationRefused):
            sources_from_context({'readonly_sources': dict(raw, steam=str(self.base))})
        with self.assertRaises(IsolationRefused):
            sources_from_context({'readonly_sources': dict(raw, proton=str(self.base / 'missing'))})


if __name__ == '__main__':
    unittest.main()
