import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.testing import client_plan as c


class ClientPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.protected = self.base / 'original'; self.protected.mkdir()
        self.scope = self.base / 'owned'
        self.scope.mkdir(mode=0o700)
        self.override = patch.object(c, 'BASE', self.scope); self.override.start()
        self.identifier = '0' * 31 + '1'
        self.root = c.construct(self.scope / 'profiles', [str(self.protected)], self.identifier)

    def tearDown(self):
        self.override.stop(); self.tmp.cleanup()

    def test_fresh_empty_private_client_and_exact_closed_commands(self):
        manifest = c.validate(self.root)
        self.assertEqual(manifest['copies'], 0)
        self.assertFalse(manifest['client_game_launch'])
        expected = c.expected_for(self.root, manifest)
        self.assertLess(len(os.fsencode(expected['fs_socket'])),108)
        self.assertEqual(Path(expected['fs_socket']).parent,self.scope)
        outer = c.outer_command(self.root, expected)
        inner = c.nested_command(expected)
        self.assertNotIn('--disable-userns', outer)
        self.assertIn('--disable-userns', inner)
        for args in [outer, inner]:
            for flag in ['--unshare-all', '--unshare-user', '--cap-drop', '--clearenv', '--new-session', '--die-with-parent']:
                self.assertIn(flag, args)
            self.assertNotIn('--share-net', args)
            self.assertNotIn('/usr/games/steam', args)
        binds = [outer[i+1:i+3] for i,arg in enumerate(outer) if arg=='--bind']
        self.assertEqual(binds, [[str(self.root/'work'),'/work'],[str(self.root/'work/home'),'/home/test']])
        self.assertTrue(all(not list((self.root / p).iterdir()) for p in c.DIRECTORIES if p.startswith('work/') and p not in {'work/home','work/home/.steam','work/home/.steam/debian-installation'}))

    def test_reuse_overlap_relative_traversal_and_link_scope_refused(self):
        with self.assertRaises(c.Refused):c.construct(self.scope/'profiles',[str(self.protected)],self.identifier)
        for protected in [['relative'],[str(self.scope)],['/tmp/../tmp']]:
            with self.assertRaises(c.Refused):c.construct(self.scope/'profiles',protected,'2'*32)
        alias=self.base/'alias';alias.symlink_to(self.scope,target_is_directory=True)
        with self.assertRaises(c.Refused):c.construct(alias/'profiles',[str(self.protected)],'3'*32)

    def test_symlink_hardlink_nonempty_and_unknown_manifest_refused(self):
        target=self.root/'work/alien'
        target.symlink_to(self.protected)
        with self.assertRaises(c.Refused):c.validate(self.root)
        target.unlink()
        os.link(self.root/'protected/marker',target)
        with self.assertRaises(c.Refused):c.validate(self.root)
        target.unlink()
        target.write_text('unexpected')
        with self.assertRaises(c.Refused):c.validate(self.root)
        target.unlink()
        manifest=json.loads((self.root/'manifest.json').read_text());manifest['command']=['steam']
        c.save(self.root/'manifest.json',manifest)
        with self.assertRaises(c.Refused):c.validate(self.root)

    def test_expected_command_mount_environment_and_namespace_injection_refused(self):
        expected=c.expected_for(self.root,c.validate(self.root))
        for name,value in [('command',['steam']),('extra_mount','/'),('environment',{'HOME':'/original'})]:
            other=dict(expected);other[name]=value
            with self.assertRaises(c.Refused):c.outer_command(self.root,other)
            with self.assertRaises(c.Refused):c.nested_command(other)
        for name,value in [('tcp_port',9),('protected',[]),('host_ns',{}),('outer_ns',{'pid':'same'})]:
            other=dict(expected);other[name]=value
            with self.assertRaises(c.Refused):c.outer_command(self.root,other)

    def test_changed_canary_directory_or_profile_inode_refused(self):
        marker=self.root/'protected/marker';marker.write_bytes(b'changed')
        with self.assertRaises(c.Refused):c.validate(self.root)
        marker.write_bytes(c.MARKER)
        (self.root/'work/cache').chmod(0o755)
        with self.assertRaises(c.Refused):c.validate(self.root)
        (self.root/'work/cache').chmod(0o700)
        old=self.root.with_name('old');self.root.rename(old)
        self.root.mkdir(mode=0o700)
        (old/'manifest.json').rename(self.root/'manifest.json')
        with self.assertRaises(c.Refused):c.validate(self.root)

    def test_no_gate_or_forged_gate_cannot_start_subprocess(self):
        with patch.object(c.subprocess,'Popen') as popen:
            with self.assertRaises(FileNotFoundError):c.execute(self.root)
            c.save(self.scope/'gate.json',{'judge':'PASS'})
            c.save(self.scope/'prelaunch.json',{})
            with self.assertRaises(c.Refused):c.execute(self.root)
            popen.assert_not_called()

    def test_boundary_failure_never_runs_nested_and_is_not_host_denial(self):
        expected=c.expected_for(self.root,c.validate(self.root))
        for failure in ['readonly_mounts','mounts_exact_allowlist','all_host_namespaces_separate','canary_unmount_denied']:
            with patch.object(c,'boundary',return_value={'checks':{failure:False}}),patch.object(c.subprocess,'run') as run,patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(c.inside(expected,'outer'),1)
                run.assert_not_called()
        denial='bwrap: Creating new namespace failed: Operation not permitted\n'
        self.assertEqual(c.classify(1,denial,True),'NESTED_DRY_HOST_DENIAL_CLASSIFIED')
        installed_denial=('bwrap: No permissions to create a new namespace, likely because the kernel does not allow '
            'non-privileged user namespaces. See <https://deb.li/bubblewrap> or '
            '<file:///usr/share/doc/bubblewrap/README.Debian.gz>.\n')
        self.assertEqual(c.classify(1,installed_denial,True),'NESTED_DRY_HOST_DENIAL_CLASSIFIED')
        self.assertEqual(c.classify(1,installed_denial+'unexpected error',True),'FAIL')
        self.assertEqual(c.classify(1,'bwrap: No permissions to create new namespace',True),'FAIL')
        for args in [(1,denial,False),(124,denial,True),(1,'bwrap: Unknown option',True),(1,denial+'unrelated error',True)]:
            self.assertEqual(c.classify(*args),'FAIL')

    def test_existing_output_symlink_hardlink_and_reuse_block_execution(self):
        external=self.base/'external';external.write_bytes(b'original')
        output=self.scope/'result.json'
        for kind in ['symlink','hardlink','regular']:
            if kind=='symlink':output.symlink_to(external)
            elif kind=='hardlink':os.link(external,output)
            else:output.write_bytes(b'old result')
            with patch.object(c.subprocess,'Popen') as popen:
                with self.assertRaises(c.Refused):c.execute(self.root)
                popen.assert_not_called()
            self.assertEqual(external.read_bytes(),b'original')
            output.unlink()

    def test_output_inserted_after_preflight_never_overwrites_target(self):
        external=self.base/'external';external.write_bytes(b'original')
        output=self.scope/'result.json'
        for kind in ['symlink','hardlink','regular']:
            c.require_new_result()
            if kind=='symlink':output.symlink_to(external)
            elif kind=='hardlink':os.link(external,output)
            else:output.write_bytes(b'old result')
            with self.assertRaises(c.Refused):c.write_result({'exit':0})
            self.assertEqual(external.read_bytes(),b'original')
            if kind=='regular':self.assertEqual(output.read_bytes(),b'old result')
            output.unlink()

    def test_output_collision_during_exclusive_open_retains_failed_run(self):
        external=self.base/'external';external.write_bytes(b'original')
        output=self.scope/'result.json'
        real_open=os.open
        result={'exit':0,'canary_unchanged':True}
        for kind in ['symlink','hardlink','regular']:
            def insert_then_open(path, flags, mode=0o777):
                if Path(path)==output:
                    if kind=='symlink':output.symlink_to(external)
                    elif kind=='hardlink':os.link(external,output)
                    else:output.write_bytes(b'old result')
                return real_open(path,flags,mode)
            with patch.object(c.os,'open',side_effect=insert_then_open):
                with self.assertRaises(c.Refused) as caught:c.write_result(result)
            self.assertEqual(str(caught.exception),'RESULT_PUBLICATION_COLLISION')
            self.assertIs(caught.exception.evidence,result)
            self.assertEqual(external.read_bytes(),b'original')
            if kind=='regular':self.assertEqual(output.read_bytes(),b'old result')
            output.unlink()

    def test_occupied_short_socket_is_preserved_before_any_listener(self):
        expected=c.expected_for(self.root,c.validate(self.root))
        registration={'contract':c.CONTRACT,'profile':str(self.root),
            'files':{str(self.root/p):c.sha(self.root/p) for p in ['manifest.json','protected/marker','host-only']},
            'metadata':{},'expected':expected,'command':c.outer_command(self.root,expected),
            'candidate_sha256':'synthetic-only','absent_output':str(self.scope/'result.json')}
        c.save(self.scope/'prelaunch.json',registration)
        c.save(self.scope/'gate.json',{'contract':c.CONTRACT,
            'registration_sha256':c.sha(self.scope/'prelaunch.json'),
            'judge':'PASS','governor':'APPROVE_DRY_PROBE'})
        occupied=Path(expected['fs_socket']);occupied.write_bytes(b'other owner')
        with patch.object(c.subprocess,'Popen') as popen,patch.object(c.socket,'socket') as listener:
            with self.assertRaises(c.Refused) as caught:c.execute(self.root)
            self.assertEqual(str(caught.exception),'FILESYSTEM_SOCKET_LENGTH_OR_REUSE')
            popen.assert_not_called();listener.assert_not_called()
        self.assertEqual(occupied.read_bytes(),b'other owner')

    def test_exact_minimal_device_mounts_and_unknown_or_writable_source_refused(self):
        mounts={p:['ro'] for p in ['/', '/usr','/protected-canary','/client_plan.py','/probe.py']}
        mounts.update({'/dev/'+name:['rw'] for name in c.SAFE_DEVICES})
        self.assertTrue(all(c.mount_checks(mounts).values()))
        for name in ['/dev/dri','/dev/input','/dev/unknown','/host']:
            changed={**mounts,name:['rw']}
            self.assertFalse(c.mount_checks(changed)['mounts_exact_allowlist'])
            self.assertFalse(c.mount_checks(changed)['writable_mounts_bounded'])
        for name in ['/','/usr','/protected-canary','/client_plan.py']:
            changed={**mounts,name:['rw']}
            self.assertFalse(c.mount_checks(changed)['readonly_mounts'])
            self.assertFalse(c.mount_checks(changed)['writable_mounts_bounded'])


if __name__=='__main__':unittest.main()
