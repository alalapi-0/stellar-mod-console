import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.testing import bootstrap, bootstrap_driver
from src.testing.isolation import IsolationRefused, digest


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / '.local/r04/bootstrap-tests'
        parent.mkdir(exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(tmp.cleanup); self.base = Path(tmp.name)
        self.runtime = self.base / 'proton'; self.runtime.mkdir()
        self.game = self.base / 'game'; self.game.mkdir()
        for name in ['LICENSE','LICENSE.OFL','PATENTS.AV1','version','dist.lock','proton']:
            (self.runtime / name).write_text('synthetic licensed fixture ' + name)
        records = []
        for p in sorted(self.runtime.iterdir()):
            s=p.stat(); records.append({'relative':p.name,'sha256':digest(p),
                'stamp':[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]})
        self.preflight={'runtime_root':str(self.runtime),'regular_files':records,'runtime_symlinks':[],
                        'fixup_marker':'same','fixup_source_mtime':'same','user_settings_exists':False,'legacy_dist_exists':False}
        self.context={'readonly_sources':{'game':str(self.game),'proton':str(self.runtime)},
                      'protected_roots':[str(self.game),str(self.runtime)]}
        self.private=self.base / 'private'; self.private.mkdir()

    def create(self):
        return bootstrap.prepare(self.private,self.context,self.preflight)

    def test_fresh_namespace_is_closed_and_masks_sources(self):
        root=self.create(); plan=bootstrap.validate_fresh(root,self.preflight)
        args=bootstrap.namespace_command(root,{'mode':'dry','expected':{}})
        self.assertNotIn('/game',args)
        self.assertEqual([(args[i+1],args[i+2]) for i,v in enumerate(args) if v=='--bind'],
            [(str(root/'work'),'/work'),(str(root/'work/proton-dist.lock'),'/proton/dist.lock'),(str(root/'work/home'),'/home/test')])
        for target in bootstrap.MASKS:
            self.assertIn(['--ro-bind',str(root/'work/empty-mask'),target], [args[i:i+3] for i in range(len(args))])
        self.assertEqual(plan['environment']['STEAM_COMPAT_DATA_PATH'],'/work/compatdata')
        self.assertEqual(plan['environment']['STEAM_COMPAT_CLIENT_INSTALL_PATH'],'/work/fake-steam')
        self.assertNotIn('SteamGameId',plan['environment'])
        self.assertEqual(bootstrap_driver.COMMANDS[0],['/usr/bin/python3','-B','/proton/proton','getcompatpath','/work'])
        self.assertEqual(bootstrap_driver.COMMANDS[1][3:7],['runinprefix','C:\\windows\\system32\\cmd.exe','/d','/c'])
        self.assertEqual(bootstrap_driver.COMMANDS[1][7], 'echo STELLAR_R04_B_SENTINEL>C:\\stellar-r04-bootstrap.txt&&type C:\\stellar-r04-bootstrap.txt')
        self.assertEqual(bootstrap_driver.COMMANDS[2],['/proton/files/bin/wineserver','-w'])
        self.assertEqual(bootstrap_driver.SENTINEL,b'STELLAR_R04_B_SENTINEL\r\n')
        self.assertEqual(bootstrap_driver.TIMEOUTS,[40,15,15])

    def test_no_extra_environment_masks_or_prefix_reuse(self):
        for mutation in ['environment','masks','reuse']:
            root=self.create();path=root/'bootstrap-plan.json'; plan=json.loads(path.read_text())
            if mutation=='environment':plan['environment']['DISPLAY']=':0'
            if mutation=='masks':plan['masks'].append('/host')
            if mutation=='reuse':(root/'work/compatdata/pfx').mkdir()
            path.write_text(json.dumps(plan))
            with self.assertRaises(IsolationRefused):bootstrap.validate_fresh(root,self.preflight)

    def test_changed_runtime_or_preflight_refused(self):
        root=self.create()
        with self.assertRaises(IsolationRefused):
            bootstrap.validate_fresh(root,dict(self.preflight,unexpected='extra'))
        (self.runtime/'proton').write_text('changed runtime')
        with self.assertRaises(IsolationRefused):bootstrap.validate_fresh(root,self.preflight)

    def test_prefix_symlinks_never_followed_during_manifest(self):
        tree=self.base/'tree';tree.mkdir();secret=self.base/'not-in-tree';secret.mkdir()
        (secret/'marker').write_text('must not be read or included')
        (tree/'z:').symlink_to('/',target_is_directory=True)
        (tree/'outside').symlink_to(secret,target_is_directory=True)
        result=bootstrap.nonfollowing_tree(tree)
        self.assertEqual({x['relative'] for x in result},{'z:','outside'})
        self.assertTrue(all(x['type']=='symlink' for x in result))
        root=self.create();(root/'work/escape').symlink_to(secret)
        with self.assertRaises(IsolationRefused):bootstrap.validate_fresh(root,self.preflight)

    def test_execute_needs_exact_gate_and_changed_source_refuses(self):
        root=self.create()
        gate=self.private/'bootstrap-launch-registration.json'
        gate.write_text(json.dumps({'approval':'PENDING','profile':str(root)}))
        with self.assertRaises(IsolationRefused):bootstrap.run_mode(root,'execute',self.preflight)
        gate.write_text(json.dumps({'approval':'APPROVE_LAUNCH','profile':str(root),'files':{str(self.runtime/'proton'):'0'*64}}))
        with self.assertRaises(IsolationRefused):bootstrap.run_mode(root,'execute',self.preflight)
        with self.assertRaises(IsolationRefused):bootstrap.run_mode(root,'arbitrary',self.preflight)

    def test_execute_refuses_changed_protected_preimage_before_launch(self):
        root=self.create(); canary=self.game/'owned-fixture'; canary.write_text('before')
        before=bootstrap.protected_snapshot({'hashed':[{'path':str(canary)}], 'game_tree_metadata':[]})
        preimages=self.private/'before.json'; preimages.write_text(json.dumps(before))
        gate={'approval':'APPROVE_LAUNCH','profile':str(root),'files':{},'protected_preimages':str(preimages)}
        (self.private/'bootstrap-launch-registration.json').write_text(json.dumps(gate))
        canary.write_text('concurrent change')
        with patch('subprocess.Popen') as launch:
            with self.assertRaises(IsolationRefused):bootstrap.run_mode(root,'execute',self.preflight)
            launch.assert_not_called()

    def test_cli_rejects_extra_command_before_file_access(self):
        with patch('sys.argv',['bootstrap','execute','--profile','/unowned','--command','anything']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as caught:
                bootstrap.main()
        self.assertEqual(caught.exception.code,2)


if __name__ == '__main__':unittest.main()
