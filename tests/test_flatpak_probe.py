import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.testing import flatpak_probe as probe


class Completed:
    def __init__(self, code, out='', err=''):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def payload(**overrides):
    body = {key: True for key in probe.BASE_CHECKS}
    body['nvidia_marker_present'] = True
    body['nvidia_marker_ambiguous'] = False
    body.update(overrides)
    return json.dumps(body)


class FlatpakProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.scope = self.base / 'owned'
        self.scope.mkdir(mode=0o700)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.patch = patch.object(probe, 'BASE', self.scope)
        self.patch.start()
        self.identifier = 'a' * 32
        self.root = probe.construct(self.scope / 'profiles', self.identifier)
        self.identity = probe.pinned_identity()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def arm(self):
        registration = self.scope / 'prelaunch.json'
        probe.save(registration, {'unit': 'R04-n', 'candidate': 'fixed'})
        digest = hashlib.sha256(registration.read_bytes()).hexdigest()
        probe.save(self.scope / 'implementation.json', {
            'contract_id': probe.CONTRACT,
            'semantic_candidate': probe.semantic_candidate(),
        })
        probe.save(self.scope / 'gate.json', {
            'contract': probe.CONTRACT, 'registration_sha256': digest,
            'judge': 'PASS', 'governor': 'APPROVE_FLATPAK_PROBE'})
        return digest

    def test_closed_commands_deny_sockets_devices_and_host_filesystem(self):
        build = self.root / 'build'
        init = probe.build_init_command(build)
        command = probe.build_command(build)
        self.assertEqual(init[:2], [probe.FLATPAK, 'build-init'])
        self.assertNotIn('--writable-sdk', init)
        self.assertNotIn('--sdk-extension', init)
        self.assertNotIn('--base', init)
        self.assertNotIn('--var', init)
        self.assertNotIn('--update', init)
        self.assertEqual(init[-3:], [probe.RUNTIME_NAME, probe.RUNTIME_NAME, probe.BRANCH])
        for flag in ('--runtime', '--readonly', '--die-with-parent', '--unshare=network', '--unshare=ipc'):
            self.assertIn(flag, command)
        for name in probe.SOCKETS:
            self.assertIn('--nosocket=' + name, command)
            self.assertNotIn('--socket=' + name, command)
        for name in probe.DEVICES:
            self.assertIn('--nodevice=' + name, command)
        for name in ('host', 'home', 'host-os', 'host-etc'):
            self.assertIn('--nofilesystem=' + name, command)
        self.assertEqual(command[-4:], ['--build-dir=/app', str(build), '/bin/sh', '/app/probe.sh'])
        self.assertIn('--filesystem=host:reset', command)
        self.assertNotIn('--with-appdir', command)
        self.assertNotIn('bwrap', command)
        self.assertTrue((self.root / 'build').is_dir())
        self.assertEqual(list((self.root / 'build').iterdir()), [])
        self.assertNotIn(probe.SENTINEL_NAME, [p.name for p in (self.root / 'build').rglob('*')])

    def test_injection_missing_denial_and_unsupported_options_refused(self):
        build = str(self.root / 'build')
        command = probe.build_command(build)
        for extra in ('--with-appdir', '--sandbox', '--bind-mount=/tmp=/tmp', '--env=HOME=/root',
                      '--filesystem=home', '--share=network', '--socket=x11', '--device=dri',
                      '--allow=devel', '/usr/bin/bwrap'):
            mutated = command + [extra]
            with self.assertRaises(probe.Refused):
                probe.validate_build_command(mutated, build)
        missing = [arg for arg in command if arg != '--nosocket=x11']
        with self.assertRaises(probe.Refused) as denied:
            probe.validate_build_command(missing, build)
        self.assertEqual(str(denied.exception), 'MISSING_DENIAL')
        with self.assertRaises(probe.Refused):
            probe.build_command(self.scope / 'escape')
        with self.assertRaises(probe.Refused):
            probe.build_init_command(Path(build + '/../../tmp'))
        with self.assertRaises(probe.Refused):
            probe.launch_environment({'HOME': '/root', 'XDG_CONFIG_HOME': '/tmp'})
        launched = probe.launch_environment(None)
        self.assertEqual(launched['HOME'], os.environ['HOME'])
        self.assertNotIn('DISPLAY', launched)
        self.assertNotIn('FLATPAK_USER_DIR', launched)

    def test_reuse_symlink_hardlink_and_foreign_payload_refused(self):
        with self.assertRaises(probe.Refused):
            probe.construct(self.scope / 'profiles', self.identifier)
        build = self.root / 'build'
        alias = build / 'link'
        alias.symlink_to(self.home)
        with self.assertRaises(probe.Refused):
            probe.validate(self.root)
        alias.unlink()
        os.link(self.root / 'probe.sh', build / 'hard')
        with self.assertRaises(probe.Refused):
            probe.validate(self.root)
        (build / 'hard').unlink()
        (self.root / 'probe.sh').chmod(0o700)
        (self.root / 'probe.sh').write_bytes(b'#!/bin/sh\necho foreign\n')
        with self.assertRaises(probe.Refused):
            probe.validate(self.root)

    def test_unexpected_metadata_and_identity_refused(self):
        manifest = json.loads((self.root / 'manifest.json').read_text())
        manifest['command'] = ['flatpak', 'run']
        probe.save(self.root / 'manifest.json', manifest)
        with self.assertRaises(probe.Refused):
            probe.validate(self.root)
        other = dict(self.identity)
        other['platform_metadata_sha256'] = 'c' * 64
        with self.assertRaises(probe.Refused):
            probe.require_identity(other)
        other = dict(self.identity)
        other['payload_sha256'] = 'd' * 64
        with self.assertRaises(probe.Refused):
            probe.require_identity(other)
        other = dict(self.identity)
        other['nvidia_metadata_sha256'] = 'b' * 64
        with self.assertRaises(probe.Refused):
            probe.require_identity(other)
        extra = dict(self.identity)
        extra['home'] = '/tmp'
        with self.assertRaises(probe.Refused):
            probe.require_identity(extra)

    def test_classification_keeps_timeout_denial_and_false_success_distinct(self):
        proved = payload()
        self.assertEqual(probe.classify_build(0, proved, ''),
                         'FLATPAK_RUNTIME_BOUNDARY_PROVED_WITH_DEFAULT_NVIDIA_EXTENSION')
        partial = payload(nvidia_marker_present=False, nvidia_marker_ambiguous=True)
        self.assertEqual(probe.classify_build(0, partial, ''),
                         'FLATPAK_BASE_BOUNDARY_PROVED_NVIDIA_EXTENSION_NOT_PROVED')
        failed = payload(app_readonly=False)
        self.assertEqual(probe.classify_build(0, failed, ''), 'PROBE_FAILED_UNCLASSIFIED')
        self.assertNotEqual(probe.classify_build(0, failed, ''),
                            'FLATPAK_RUNTIME_BOUNDARY_PROVED_WITH_DEFAULT_NVIDIA_EXTENSION')
        denial = probe.HOST_EXACT[1]
        self.assertEqual(probe.classify_build(1, '', denial), 'FLATPAK_HOST_DENIAL_CLASSIFIED')
        self.assertEqual(probe.classify_build(1, proved, denial), 'PROBE_FAILED_UNCLASSIFIED')
        self.assertEqual(probe.classify_init(1, denial + '\nextra'), 'PROBE_FAILED_UNCLASSIFIED')
        self.assertEqual(probe.classify_init(1, probe.PREREQ_EXACT[0]),
                         'FLATPAK_SUPPORTED_PREREQUISITE_MISSING')
        self.assertEqual(probe.classify_init(1, probe.PREREQ_EXACT[0] + '\nmore'),
                         'PROBE_FAILED_UNCLASSIFIED')
        self.assertEqual(probe.classify_build(2, '', 'error: Unknown option --sandbox'),
                         'PROBE_ARGUMENT_DEFECT')
        self.assertEqual(probe.classify_timeout(), 'PROBE_TIMEOUT')
        self.assertEqual(probe.classify_build(0, 'x' * (probe.OUTPUT_LIMIT + 1), ''),
                         'PROBE_FAILED_UNCLASSIFIED')
        self.assertEqual(probe.apply_residue(
            'FLATPAK_RUNTIME_BOUNDARY_PROVED_WITH_DEFAULT_NVIDIA_EXTENSION', True),
            'PROBE_FAILED_UNCLASSIFIED')

    def test_script_has_no_launcher_and_parses(self):
        text = probe.payload_bytes().decode()
        self.assertIn('R04N_SELF_AUTHORED_PROBE', text)
        for banned in ('flatpak run', 'bwrap', 'curl ', 'wget ', '/usr/bin/bwrap'):
            self.assertNotIn(banned, text)
        path = self.base / 'probe.sh'
        path.write_bytes(probe.payload_bytes())
        syntax = subprocess.run(['/bin/sh', '-n', str(path)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_missing_gate_output_reuse_and_init_failure_do_not_build(self):
        calls = []

        def runner(args, timeout, env):
            calls.append((args, env))
            return Completed(1, '', probe.PREREQ_EXACT[0])

        with self.assertRaises(probe.Refused):
            probe.execute(self.root, self.identity, '0' * 64, runner, self.home)
        self.assertEqual(calls, [])
        external = self.base / 'external'
        external.write_bytes(b'original')
        output = self.scope / 'result.json'
        output.symlink_to(external)
        digest = self.arm()
        with self.assertRaises(probe.Refused):
            probe.execute(self.root, self.identity, digest, runner, self.home)
        self.assertEqual(external.read_bytes(), b'original')
        self.assertEqual(calls, [])
        output.unlink()
        outcome = probe.execute(self.root, self.identity, digest, runner, self.home)
        self.assertEqual(outcome, 'FLATPAK_SUPPORTED_PREREQUISITE_MISSING')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1], 'build-init')
        self.assertEqual(calls[0][1]['HOME'], os.environ['HOME'])
        self.assertNotIn('DISPLAY', calls[0][1])

    def test_timeout_and_residue_never_claim_success(self):
        digest = self.arm()

        def timeout(args, timeout_seconds, env):
            raise subprocess.TimeoutExpired(args, timeout_seconds)

        self.assertEqual(probe.execute(self.root, self.identity, digest, timeout, self.home), 'PROBE_TIMEOUT')
        (self.scope / 'result.json').unlink()

        def succeed(args, timeout_seconds, env):
            if args[1] == 'build-init':
                (self.root / 'build' / 'files').mkdir()
                return Completed(0)
            (self.home / '.var' / 'app' / probe.APP_ID).mkdir(parents=True)
            return Completed(0, payload(), '')

        self.assertEqual(probe.execute(self.root, self.identity, digest, succeed, self.home),
                         'PROBE_FAILED_UNCLASSIFIED')
        result = json.loads((self.scope / 'result.json').read_text())
        self.assertTrue(result['platform_25_08_is_not_steam_26_08'])
        self.assertNotEqual(result['outcome'], 'FLATPAK_RUNTIME_BOUNDARY_PROVED_WITH_DEFAULT_NVIDIA_EXTENSION')

    def test_private_mode_ancestor_change_and_registration_binding(self):
        self.scope.chmod(0o755)
        with self.assertRaises(probe.Refused):
            probe.validate(self.root)
        self.scope.chmod(0o700)
        snapshot = probe.ancestor_snapshot(self.root)
        self.scope.chmod(0o750)
        with self.assertRaises(probe.Refused):
            probe.assert_snapshot(snapshot)
        self.scope.chmod(0o700)
        calls = []

        def runner(args, timeout, env):
            calls.append(args)

        digest = self.arm()
        registration = json.loads((self.scope / 'implementation.json').read_text())
        registration['semantic_candidate'] = '0' * 64
        probe.save(self.scope / 'implementation.json', registration)
        with self.assertRaises(probe.Refused):
            probe.execute(self.root, self.identity, digest, runner, self.home)
        self.assertEqual(calls, [])
        text = probe.payload_bytes().decode()
        self.assertIn('/app/sentinel.path', text)
        self.assertIn('/app/nvidia.sha256', text)
        self.assertIn(probe.PINNED_NVIDIA_SHA, probe.pinned_identity()['nvidia_metadata_sha256'])
