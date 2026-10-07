import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.testing import flatpak_probe as probe


class Done:
    def __init__(self, code, out=b'', err=b''):
        self.returncode = code
        self.stdout = io.BytesIO(out if isinstance(out, bytes) else out.encode())
        self.stderr = io.BytesIO(err if isinstance(err, bytes) else err.encode())
        self.pid = -1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class Hang:
    def __init__(self):
        self._out_r, self._out_w = os.pipe()
        self._err_r, self._err_w = os.pipe()
        self.stdout = os.fdopen(self._out_r, 'rb')
        self.stderr = os.fdopen(self._err_r, 'rb')
        self.returncode = None
        self.pid = -1
        self.reaped = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('hang', timeout or 0)
        return self.returncode

    def terminate_owned(self):
        self.reaped = True
        self.returncode = -9
        for fd in (self._out_w, self._err_w):
            try:
                os.close(fd)
            except OSError:
                pass


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
        path = self.scope / 'implementation.json'
        probe.save(path, probe.registration_document())
        digest = probe.sha_file(path)
        probe.save(self.scope / 'gate.json', {
            'contract': probe.CONTRACT, 'registration_sha256': digest,
            'judge': 'PASS', 'governor': 'APPROVE_FLATPAK_PROBE'})
        return digest

    def launch(self, popen, **kwargs):
        with patch.object(probe, 'read_deployments', return_value=probe.pinned_identity()):
            return probe.execute(self.root, self.home, popen=popen, **kwargs)

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

        def popen(args, **kwargs):
            calls.append((args, kwargs['env']))
            return Done(1, b'', probe.PREREQ_EXACT[0])

        with self.assertRaises(probe.Refused):
            self.launch(popen)
        self.assertEqual(calls, [])
        external = self.base / 'external'
        external.write_bytes(b'original')
        output = self.scope / 'result.json'
        output.symlink_to(external)
        self.arm()
        with self.assertRaises(probe.Refused):
            self.launch(popen)
        self.assertEqual(external.read_bytes(), b'original')
        self.assertEqual(calls, [])
        output.unlink()
        outcome = self.launch(popen)
        self.assertEqual(outcome, 'FLATPAK_SUPPORTED_PREREQUISITE_MISSING')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1], 'build-init')
        self.assertEqual(calls[0][1]['HOME'], os.environ['HOME'])
        self.assertNotIn('DISPLAY', calls[0][1])
        result = json.loads((self.scope / 'result.json').read_text())
        self.assertTrue(result['postflight_app_paths_absent'])

    def test_timeout_reaps_and_residue_never_claims_success(self):
        self.arm()
        hang = Hang()

        def popen(args, **kwargs):
            return hang

        self.assertEqual(self.launch(popen, init_timeout=0.2), 'PROBE_TIMEOUT')
        self.assertTrue(hang.reaped)
        result = json.loads((self.scope / 'result.json').read_text())
        self.assertTrue(result['postflight_app_paths_absent'])
        (self.scope / 'result.json').unlink()

        def succeed(args, **kwargs):
            if args[1] == 'build-init':
                (self.root / 'build' / 'files').mkdir()
                return Done(0)
            (self.home / '.var' / 'app' / probe.APP_ID).mkdir(parents=True)
            return Done(0, payload(), b'')

        self.assertEqual(self.launch(succeed), 'PROBE_FAILED_UNCLASSIFIED')
        result = json.loads((self.scope / 'result.json').read_text())
        self.assertTrue(result['platform_25_08_is_not_steam_26_08'])
        self.assertFalse(result['postflight_app_paths_absent'])
        self.assertNotEqual(result['outcome'], 'FLATPAK_RUNTIME_BOUNDARY_PROVED_WITH_DEFAULT_NVIDIA_EXTENSION')

    def test_streaming_limit_and_timeout_reap_real_processes(self):
        huge = subprocess.Popen(
            [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"x"*80000)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        limited = probe.collect_process(huge, 5, 1024)
        self.assertTrue(limited['exceeded'])
        self.assertIsNone(limited['stdout'])
        self.assertIsNotNone(huge.poll())
        sleeper = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        timed = probe.collect_process(sleeper, 0.2, 1024)
        self.assertTrue(timed['timed_out'])
        self.assertIsNotNone(sleeper.poll())

    def test_tree_and_registration_digest_block_launch(self):
        self.arm()
        stale = probe.profile_pin(self.root)
        os.utime(self.root / 'probe.sh', None)
        calls = []

        def popen(args, **kwargs):
            calls.append(args)

        with patch.object(probe, 'profile_pin', return_value=stale):
            with self.assertRaises(probe.Refused) as changed:
                self.launch(popen)
        self.assertEqual(str(changed.exception), 'TREE_CHANGED')
        self.assertEqual(calls, [])
        (self.scope / 'implementation.json').write_bytes(b'{}\n')
        with self.assertRaises(probe.Refused):
            self.launch(popen)
        self.assertEqual(calls, [])
        self.arm()
        gate = json.loads((self.scope / 'gate.json').read_text())
        gate['registration_sha256'] = '0' * 64
        probe.save(self.scope / 'gate.json', gate)
        with self.assertRaises(probe.Refused):
            self.launch(popen)
        self.assertEqual(calls, [])

    def test_deployment_reader_binds_real_files_and_rejects_mismatch(self):
        self.assertEqual(probe.read_deployments(*probe.deployment_paths()), probe.pinned_identity())
        flatpak = self.base / 'flatpak'
        flatpak.write_bytes(b'not-the-binary')
        with self.assertRaises(probe.Refused):
            probe.read_deployments(flatpak, flatpak, flatpak)

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

        def popen(args, **kwargs):
            calls.append(args)

        self.arm()
        registration = json.loads((self.scope / 'implementation.json').read_text())
        registration['semantic_candidate'] = '0' * 64
        probe.save(self.scope / 'implementation.json', registration)
        with self.assertRaises(probe.Refused):
            self.launch(popen)
        self.assertEqual(calls, [])
        document = probe.registration_document()
        self.assertEqual(document['command_prefix'][:2], [probe.FLATPAK, 'build'])
        self.assertEqual(document['environment_allowlist'][:2], ['HOME', 'PATH'])
        self.assertIn('nvidia_marker_present', document['expected_payload_keys'])
        text = probe.payload_bytes().decode()
        self.assertIn('/app/sentinel.path', text)
        self.assertIn('/app/nvidia.sha256', text)
        self.assertIn('/proc/sysvipc/shm', text)
        self.assertIn(probe.PINNED_NVIDIA_SHA, probe.pinned_identity()['nvidia_metadata_sha256'])
