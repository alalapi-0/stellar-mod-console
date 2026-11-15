"""Fixed nested-container dry proof; no Steam, game or authentication launcher."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import uuid

INSIDE = os.sys.argv[1:2] in (['--outer'], ['--inner'])
if not INSIDE:
    from src.testing.isolation import owned_ancestors, disjoint
REPOSITORY = Path('/unmounted') if INSIDE else Path(__file__).resolve().parents[2]
BASE = REPOSITORY / '.local/r04/client-plan'
CONTRACT = 'stellar-mod-console/R04-j/v1'
NAMESPACES = ('user', 'mnt', 'pid', 'ipc', 'net', 'uts', 'cgroup')
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/home/test', 'LC_ALL': 'C.UTF-8',
       'TMPDIR': '/tmp', 'XDG_RUNTIME_DIR': '/run/test', 'XDG_CACHE_HOME': '/work/cache',
       'XDG_CONFIG_HOME': '/work/config', 'XDG_DATA_HOME': '/work/data', 'PWD': '/work'}
DIRECTORIES = ('work', 'work/home', 'work/home/.steam', 'work/home/.steam/debian-installation',
               'work/home/.steam/debian-installation/userdata', 'work/cache', 'work/config',
               'work/data', 'work/library', 'work/compatdata', 'work/prefix', 'work/logs',
               'work/quarantine', 'protected')
MARKER = b'owned readonly nested canary\n'
SAFE_DEVICES = {'null': (1, 3), 'zero': (1, 5), 'full': (1, 7),
                'random': (1, 8), 'urandom': (1, 9), 'tty': (5, 0)}


class Refused(ValueError):
    pass


def sha(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode): raise Refused('HASH_NOT_REGULAR')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        identity = lambda st: [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
        if identity(before) != identity(after) or identity(after) != stamp(path): raise Refused('HASH_FILE_CHANGED')
    return digest


def stamp(path):
    st = Path(path).lstat()
    return [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def namespaces():
    return {k: os.readlink('/proc/self/ns/' + k) for k in NAMESPACES}


def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def require_new_result():
    owned_ancestors(BASE)
    output = BASE / 'result.json'
    if os.path.lexists(output): raise Refused('RESULT_REUSE_OR_LINK')
    return output


def write_result(result):
    output = require_new_result()
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        failure = Refused('RESULT_PUBLICATION_COLLISION')
        failure.evidence = result  # Root can retain the bounded failed run without overwriting the target.
        raise failure from error
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, sort_keys=True, indent=2); stream.write('\n')


def private_file(path):
    path = Path(path)
    if not path.is_absolute() or BASE not in path.parents:
        raise Refused('PRIVATE_FILE_SCOPE')
    owned_ancestors(path.parent)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
        raise Refused('PRIVATE_FILE_IDENTITY')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > 1024**2 or not stat.S_ISREG(before.st_mode): raise Refused('PRIVATE_FILE_BOUND')
        data = stream.read(1024**2 + 1)
        after = os.fstat(stream.fileno())
        identity = lambda st: [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
        if identity(before) != identity(after) or stamp(path) != identity(after):
            raise Refused('PRIVATE_FILE_CHANGED')
    return json.loads(data)


def construct(parent, protected, identifier):
    parent = Path(parent)
    if parent != BASE / 'profiles' or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise Refused('FIXED_PROFILE_SCOPE')
    owned_ancestors(parent)
    root = parent / identifier
    if (not isinstance(protected, list) or any(not isinstance(p, str) for p in protected) or len(protected) != len(set(protected)) or
            any(not Path(p).is_absolute() or '..' in Path(p).parts or
                Path(p).resolve(strict=True) != Path(p) for p in protected)):
        raise Refused('PROTECTED_PATH_BOUNDARY')
    if root.exists() or root.is_symlink() or any(not disjoint(root, Path(p)) for p in protected):
        raise Refused('PROFILE_REUSE_OR_OVERLAP')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(mode=0o700)
    for name in DIRECTORIES:
        (root / name).mkdir(mode=0o700)
    (root / 'protected/marker').write_bytes(MARKER)
    (root / 'host-only').write_bytes(b'never exposed\n')
    manifest = {'contract': CONTRACT, 'root': str(root), 'directories': list(DIRECTORIES),
                'protected_roots': protected, 'root_identity': stamp(root),
                'initial_files': {'protected/marker': sha(root / 'protected/marker'),
                                  'host-only': sha(root / 'host-only')},
                'copies': 0, 'authentication': 'NOT_RUN', 'client_game_launch': False}
    save(root / 'manifest.json', manifest)
    return root


def validate(root):
    root = Path(root)
    owned_ancestors(root)
    if root.parent != BASE / 'profiles':
        raise Refused('PROFILE_SCOPE')
    manifest = private_file(root / 'manifest.json')
    if set(manifest) != {'contract', 'root', 'directories', 'protected_roots', 'root_identity',
                         'initial_files', 'copies', 'authentication', 'client_game_launch'}:
        raise Refused('UNKNOWN_MANIFEST_FIELD')
    if (manifest['contract'] != CONTRACT or manifest['root'] != str(root) or manifest['copies'] != 0 or
            manifest['client_game_launch'] is not False or manifest['authentication'] != 'NOT_RUN' or
            manifest['directories'] != list(DIRECTORIES) or stamp(root)[:3] != manifest['root_identity'][:3]):
        raise Refused('MANIFEST_CHANGED')
    if any(not Path(p).is_absolute() or '..' in Path(p).parts or not disjoint(root, Path(p))
           for p in manifest['protected_roots']):
        raise Refused('PROTECTED_PATH_BOUNDARY')
    entries = {str(p.relative_to(root)): p for p in root.rglob('*')}
    allowed = set(DIRECTORIES) | {'protected/marker', 'host-only', 'manifest.json'}
    if set(entries) != allowed:
        raise Refused('PROFILE_NOT_FRESH')
    for name, path in entries.items():
        st = path.lstat()
        if st.st_uid != os.getuid() or stat.S_ISLNK(st.st_mode) or (stat.S_ISREG(st.st_mode) and st.st_nlink != 1):
            raise Refused('PROFILE_LINK_OR_OWNER')
        if name in DIRECTORIES and (not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o700):
            raise Refused('DIRECTORY_IDENTITY')
    if manifest['initial_files'] != {'protected/marker': sha(root / 'protected/marker'), 'host-only': sha(root / 'host-only')}:
        raise Refused('CANARY_CHANGED')
    return manifest


def expected_for(root, manifest):
    return {'host_ns': namespaces(), 'outer_ns': {}, 'protected': manifest['protected_roots'] + [str(REPOSITORY.parents[1])],
            'sentinel': str(root / 'host-only'), 'fs_socket': str(BASE / ('dry-' + root.name[:16] + '.sock')),
            'abstract_socket': 'stellar-client-dry-' + root.name,
            'tcp_port': 20000 + int(root.name[:4], 16) % 30000}


def runtime_views():
    return [(str(Path(p).resolve(strict=True)), p) for p in ('/usr', '/lib', '/lib64', '/bin', '/etc/ld.so.cache')]


def outer_command(root, expected):
    manifest = validate(root)
    if expected != expected_for(root, manifest): raise Refused('FIXED_EXPECTED_IDENTITY')
    args = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--new-session', '--die-with-parent',
            '--cap-drop', 'ALL', '--clearenv']
    for source, target in runtime_views():
        args += ['--ro-bind', source, target]
    args += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run', '--dir', '/run/test',
             '--tmpfs', '/dev/shm', '--bind', str(root / 'work'), '/work',
             '--bind', str(root / 'work/home'), '/home/test',
             '--ro-bind', str(root / 'protected'), '/protected-canary',
             '--ro-bind', str(Path(__file__).resolve()), '/client_plan.py',
             '--ro-bind', str(Path(__file__).with_name('probe.py').resolve()), '/probe.py']
    for k, v in ENV.items():
        if k != 'PWD': args += ['--setenv', k, v]
    return args + ['--chdir', '/work', '--remount-ro', '/', '--', '/usr/bin/python3', '-B',
                   '/client_plan.py', '--outer', json.dumps(expected, sort_keys=True)]


def nested_command(expected):
    validate_expected(expected)
    args = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--disable-userns',
            '--new-session', '--die-with-parent', '--cap-drop', 'ALL', '--clearenv',
            '--bind', '/', '/', '--proc', '/proc', '--tmpfs', '/tmp', '--tmpfs', '/run',
            '--dir', '/run/test', '--tmpfs', '/dev/shm']
    for k, v in ENV.items():
        if k != 'PWD': args += ['--setenv', k, v]
    return args + ['--chdir', '/work', '--remount-ro', '/', '--', '/usr/bin/python3', '-B',
                   '/client_plan.py', '--inner', json.dumps(expected, sort_keys=True)]


def mount_checks(mounts):
    devices = {'/dev/' + name for name in SAFE_DEVICES}
    fixed = {'/', '/usr', '/bin', '/lib', '/lib64', '/etc/ld.so.cache', '/proc', '/dev', '/dev/pts',
             '/dev/shm', '/tmp', '/run', '/work', '/home/test', '/protected-canary', '/client_plan.py', '/probe.py'}
    writable = {'/work', '/home/test', '/proc', '/dev', '/dev/pts', '/dev/shm', '/tmp', '/run'} | devices
    return {'mounts_exact_allowlist': set(mounts) <= fixed | devices,
            'readonly_mounts': all('ro' in mounts.get(p, []) for p in ('/', '/usr', '/protected-canary', '/client_plan.py', '/probe.py')),
            'writable_mounts_bounded': {p for p, flags in mounts.items() if 'rw' in flags} <= writable}


def boundary(expected, layer):
    # The host CLI cannot enter this path: these fixed mounts must exist first.
    if (os.getcwd() != '/work' or not Path('/client_plan.py').is_file() or
            not Path('/protected-canary/marker').is_file()):
        raise Refused('INNER_NAMESPACE_REQUIRED')
    from probe import cannot_connect, rejected
    ns = namespaces()
    checks = {'all_host_namespaces_separate': all(ns[k] != expected['host_ns'][k] for k in NAMESPACES),
              'host_paths_absent': all(not Path(p).exists() for p in expected['protected']),
              'host_sentinel_absent': not Path(expected['sentinel']).exists(),
              'exact_environment': dict(os.environ) == ENV,
              'no_display_input_gpu': all(not Path(p).exists() for p in
                  ('/dev/dri', '/dev/nvidia0', '/dev/input', '/dev/snd', '/tmp/.X11-unix', '/run/user')),
              'private_runtime': sorted(p.name for p in Path('/run').iterdir()) == ['test'],
              'no_steam_or_dbus_ipc': not Path('/home/test/.steam/steam.pipe').exists() and not Path('/run/dbus').exists(),
              'payload_capabilities_dropped': next(l for l in Path('/proc/self/status').read_text().splitlines()
                  if l.startswith('CapEff:')).split()[1] == '0000000000000000',
              'network_no_default_route': len(Path('/proc/net/route').read_text().splitlines()) == 1,
              'network_only_loopback': [l.split(':')[0].strip() for l in Path('/proc/net/dev').read_text().splitlines()[2:]] == ['lo']}
    if layer == 'inner':
        checks['all_outer_namespaces_separate'] = all(ns[k] != expected['outer_ns'][k] for k in NAMESPACES)
    mounts = {l.split()[4]: l.split()[5].split(',') for l in Path('/proc/self/mountinfo').read_text().splitlines()}
    checks.update(mount_checks(mounts))
    devices = {name: Path('/dev', name).lstat() for name in SAFE_DEVICES}
    checks['minimal_device_nodes_exact'] = all(stat.S_ISCHR(st.st_mode) and
        (os.major(st.st_rdev), os.minor(st.st_rdev)) == SAFE_DEVICES[name] for name, st in devices.items())
    marker = Path('/protected-canary/marker')
    checks['canary_initial_exact'] = marker.read_bytes() == MARKER
    for name, operation in {'write': lambda: marker.write_bytes(b'changed'), 'chmod': lambda: marker.chmod(0o777),
            'rename': lambda: marker.rename('/protected-canary/moved'), 'unlink': marker.unlink,
            'create': lambda: Path('/protected-canary/new').write_bytes(b'x')}.items():
        checks['canary_' + name + '_denied'] = rejected(operation)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
    libc.mount.restype = ctypes.c_int
    libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.umount2.restype = ctypes.c_int
    # Mount operations only target our disposable canary, never any source tree.
    mount_attempts = {'bind': (b'/protected-canary', b'/protected-canary', 4096),
                      'rebind': (b'/protected-canary', b'/protected-canary', 4096 | 16384),
                      'remount_rw': (None, b'/protected-canary', 4096 | 32),
                      'move': (b'/protected-canary', b'/tmp/moved-canary', 8192)}
    Path('/tmp/moved-canary').mkdir(exist_ok=True)
    for name, (source, target, flags) in mount_attempts.items():
        rc = libc.mount(source, target, None, flags, None)
        checks['canary_' + name + '_denied'] = rc == -1 and ctypes.get_errno() in (errno.EPERM, errno.EACCES, errno.EROFS)
    rc = libc.umount2(b'/protected-canary', 0)
    checks['canary_unmount_denied'] = rc == -1 and ctypes.get_errno() in (errno.EPERM, errno.EACCES)
    for name, family, address in [('filesystem', socket.AF_UNIX, expected['fs_socket']),
            ('abstract', socket.AF_UNIX, '\0' + expected['abstract_socket']),
            ('loopback', socket.AF_INET, ('127.0.0.1', expected['tcp_port'])),
            ('external', socket.AF_INET, ('192.0.2.1', 9))]:
        checks['host_' + name + '_unreachable'] = cannot_connect(family, address)['denied']
    checks['canary_final_exact'] = marker.read_bytes() == MARKER
    (Path('/work') / ('dry-' + layer)).write_bytes(b'own private write\n')
    checks['owned_write'] = (Path('/work') / ('dry-' + layer)).read_bytes() == b'own private write\n'
    return {'checks': checks, 'namespaces': ns, 'mounts': mounts}


def classify(exit_code, stderr, outer_passed):
    # Only a precise namespace permission refusal is a host denial, not malformed CLI or timeout.
    signatures = ('bwrap: Creating new namespace failed: Operation not permitted',
                  'bwrap: No permissions to create a new namespace, likely because the kernel does not allow '
                  'non-privileged user namespaces. See <https://deb.li/bubblewrap> or '
                  '<file:///usr/share/doc/bubblewrap/README.Debian.gz>.')
    if outer_passed and exit_code == 1 and stderr.strip() in signatures:
        return 'NESTED_DRY_HOST_DENIAL_CLASSIFIED'
    return 'FAIL'


def validate_expected(expected):
    if set(expected) != {'host_ns', 'outer_ns', 'protected', 'sentinel', 'fs_socket', 'abstract_socket', 'tcp_port'}:
        raise Refused('UNKNOWN_EXPECTED_FIELD')


def inside(expected, layer):
    validate_expected(expected)
    report = boundary(expected, layer)
    if layer == 'outer' and all(report['checks'].values()):
        expected['outer_ns'] = report['namespaces']
        run = subprocess.run(nested_command(expected), capture_output=True, text=True, timeout=15, close_fds=True,
                             env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
        report['nested_exit'], report['nested_stderr'] = run.returncode, run.stderr
        report['nested'] = json.loads(run.stdout) if run.stdout.strip() else None
        report['outcome'] = ('NESTED_DRY_CAPABILITY_PROVED' if run.returncode == 0 and report['nested'] and
                             all(report['nested']['checks'].values()) else classify(run.returncode, run.stderr, True))
    else:
        report['outcome'] = 'INNER_BOUNDARIES_PASS' if all(report['checks'].values()) else 'FAIL'
    print(json.dumps(report, sort_keys=True))
    return 0 if report['outcome'] in ('INNER_BOUNDARIES_PASS', 'NESTED_DRY_CAPABILITY_PROVED', 'NESTED_DRY_HOST_DENIAL_CLASSIFIED') else 1


def execute(root):
    manifest = validate(root)
    require_new_result()  # Reject output reuse/links before opening listeners or starting a process.
    gate = private_file(BASE / 'gate.json')
    registration = private_file(BASE / 'prelaunch.json')
    identity = sha(BASE / 'prelaunch.json')
    if gate != {'contract': CONTRACT, 'registration_sha256': identity, 'judge': 'PASS', 'governor': 'APPROVE_DRY_PROBE'}:
        raise Refused('EXACT_FRESH_REVIEW_REQUIRED')
    public = {str(REPOSITORY / p) for p in ['src/testing/client_plan.py', 'src/testing/probe.py',
        'src/testing/isolation.py', 'tests/test_client_plan.py', 'src/testing/README.md',
        'docs/contracts/R04-j.json', 'docs/evidence/R04-j.json']}
    private = {str(BASE / p) for p in ['context.json', 'candidate.json', 'static-metadata.json',
        'tests.json', 'protected-before.json', 'protected-extra-before.json', 'preflight.json',
        'prelaunch-failure.json', 'launch-failure.json', 'dry-failure.json', 'denial-failure.json']}
    profile_files = {str(root / p) for p in ['manifest.json', 'protected/marker', 'host-only']}
    allowed_files = public | private | profile_files
    allowed_metadata = allowed_files | {str(root / p) for p in DIRECTORIES} | {
        str(root), '/usr/bin/bwrap', '/usr/games/steam'}
    if (set(registration) != {'contract', 'profile', 'files', 'metadata', 'expected', 'command', 'candidate_sha256', 'absent_output'} or
            registration['contract'] != CONTRACT or not set(registration['files']) <= allowed_files or
            not profile_files <= set(registration['files']) or not set(registration['metadata']) <= allowed_metadata):
        raise Refused('UNKNOWN_REGISTRATION_TARGET')
    if registration['absent_output'] != str(BASE / 'result.json'): raise Refused('FIXED_OUTPUT_REQUIRED')
    if registration['profile'] != str(root) or any(sha(p) != h for p, h in registration['files'].items()):
        raise Refused('REGISTERED_CANDIDATE_CHANGED')
    if any(stamp(p) != identity for p, identity in registration['metadata'].items()):
        raise Refused('REGISTERED_METADATA_CHANGED')
    expected = registration['expected']
    args = outer_command(root, expected)
    if args != registration['command'] or namespaces() != expected['host_ns']:
        raise Refused('COMMAND_OR_HOST_NAMESPACE_CHANGED')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise Refused('PIDFD_REQUIRED')
    fs_path = Path(expected['fs_socket'])
    if len(os.fsencode(fs_path)) >= 108 or os.path.lexists(fs_path):
        raise Refused('FILESYSTEM_SOCKET_LENGTH_OR_REUSE')
    sockets = []; process = None; pidfd = None; fs_identity = None
    try:
        for family, address in [(socket.AF_UNIX, expected['fs_socket']),
                (socket.AF_UNIX, '\0' + expected['abstract_socket']),
                (socket.AF_INET, ('127.0.0.1', expected['tcp_port']))]:
            listener = socket.socket(family, socket.SOCK_STREAM); sockets.append(listener)
            listener.bind(address)
            if family == socket.AF_UNIX and address == str(fs_path):
                fs_identity = stamp(fs_path)
            listener.listen(1)
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, close_fds=True, start_new_session=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
        pidfd = os.pidfd_open(process.pid)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=25)
        except subprocess.TimeoutExpired:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            timed_out = True
        try:
            report = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            report = None
        result = {'exit': process.returncode, 'report': report, 'timeout': timed_out,
                  'raw_stdout_on_parse_failure': stdout if report is None else None,
                  'stderr': stderr, 'owned_pid': process.pid, 'owned_reaped': process.poll() is not None,
                  'cleanup_identity': 'OWNED_PIDFD; no name-based or foreign process signaling',
                  'canary_unchanged': (root / 'protected/marker').read_bytes() == MARKER,
                  'Steam_game_Proton_pressure_vessel_launches': 0}
        try:
            write_result(result)
        except Refused as failure:
            failure.evidence = result
            raise
        if timed_out or result['exit'] != 0 or not result['report'] or not result['canary_unchanged']:
            raise Refused('DRY_PROBE_FAILED')
        return result
    finally:
        if process is not None and process.poll() is None:
            if pidfd is not None: signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            process.wait(timeout=5)
        if pidfd is not None: os.close(pidfd)
        for listener in sockets: listener.close()
        if fs_identity is not None and os.path.lexists(fs_path):
            if stamp(fs_path) != fs_identity: raise Refused('OWNED_SOCKET_REPLACED')
            fs_path.unlink()


def main():
    # Inner modes have no configurable executable/mount/write target.
    if len(os.sys.argv) == 3 and os.sys.argv[1] in ('--outer', '--inner'):
        return inside(json.loads(os.sys.argv[2]), os.sys.argv[1][2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'dry'])
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare':
        if args.profile is not None: raise Refused('PREPARE_PROFILE_FORBIDDEN')
        context = private_file(BASE / 'context.json')
        if set(context) != {'protected_roots'}: raise Refused('UNKNOWN_CONTEXT_FIELD')
        root = construct(BASE / 'profiles', context['protected_roots'], uuid.uuid4().hex)
        print(json.dumps({'profile': str(root), 'client_game_launch': False}))
    else:
        if args.profile is None: raise Refused('FIXED_PROFILE_REQUIRED')
        result = execute(args.profile.absolute())
        print(json.dumps({'dry_outcome': result['report']['outcome'], 'client_game_launch': False}))
    return 0


if __name__ == '__main__':
    if os.sys.argv[1:2] in (['--outer'], ['--inner']):
        # Source package is intentionally not mounted in the dry namespace.
        os.sys.path.insert(0, '/')
    raise SystemExit(main())
