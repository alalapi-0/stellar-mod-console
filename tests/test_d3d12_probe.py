"""Build the actual fixed PE; reject changed executable identity and CLI scope."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.testing.d3d12_probe import build, validate_image, collect_owned_child, create_test_authority


class WindowsProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        (cls.root / 'work/home').mkdir(parents=True)
        cls.registration = build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_actual_pe_compiles_and_external_binutils_reads_it(self):
        p = subprocess.run(['/usr/bin/objdump', '-p', str(self.root / 'probe.exe')],
                           capture_output=True, text=True, check=True)
        self.assertIn('pei-x86-64', p.stdout)
        self.assertIn('DLL Name: KERNEL32.dll', p.stdout)
        self.assertIn('LoadLibraryExA', p.stdout)
        self.assertNotIn('DLL Name: d3d12.dll', p.stdout)
        self.assertFalse((self.root / 'build/import-stub.dll').exists())
        self.assertTrue(all(x['exit'] == 0 for x in json.loads((self.root / 'compile.json').read_text())))

    def test_unregistered_dll_import_is_rejected(self):
        data = (self.root / 'probe.exe').read_bytes()
        self.assertEqual(data.count(b'KERNEL32.dll\0'), 1)
        changed = self.root / 'changed.exe'
        changed.write_bytes(data.replace(b'KERNEL32.dll\0', b'STEAMAPI.dll\0'))
        with self.assertRaises(ValueError): validate_image(changed)

    def test_damaged_image_is_rejected(self):
        changed = self.root / 'damaged.exe'; changed.write_bytes(b'MZ\0')
        with self.assertRaises(ValueError): validate_image(changed)

    def test_configurable_command_is_rejected_before_preparation(self):
        p = subprocess.run([sys.executable, '-B', '-m', 'src.testing.d3d12_probe', '/bin/sh'],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('No configurable launch/profile/environment argument', p.stderr)

    def test_timeout_ends_owned_group_and_drains_inherited_pipes(self):
        child = subprocess.Popen([sys.executable, '-c',
            'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print("owned diagnostic",flush=True)'], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        result = collect_owned_child(child, 0.2)
        self.assertTrue(result['timeout'])
        self.assertIn('owned diagnostic', result['stdout'])
        self.assertIsNotNone(child.poll())

    def test_test_authority_is_private_fresh_and_refuses_reuse(self):
        path = self.root / 'test-authority'
        create_test_authority(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(path.is_symlink())
        self.assertIn(b'MIT-MAGIC-COOKIE-1', path.read_bytes())
        with self.assertRaises(FileExistsError): create_test_authority(path)
        path.unlink()

    def test_test_authority_refuses_symlink_target(self):
        original = self.root / 'protected-fixture'; original.write_bytes(b'original')
        link = self.root / 'authority-link'; link.symlink_to(original)
        with self.assertRaises(FileExistsError): create_test_authority(link)
        self.assertEqual(original.read_bytes(), b'original')


if __name__ == '__main__': unittest.main()
