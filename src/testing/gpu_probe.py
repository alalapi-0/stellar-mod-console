"""Fixed native headless GPU prerequisite experiment; cannot launch game/Steam."""

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
import sys
import uuid

DEVICES = ('/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm')
DRIVER = '/usr/share/vulkan/icd.d/nvidia_icd.json'
ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'HOME': '/work/home', 'LC_ALL': 'C.UTF-8',
               'TMPDIR': '/tmp', 'XDG_RUNTIME_DIR': '/run/test', 'XDG_CACHE_HOME': '/work/cache',
               'VK_DRIVER_FILES': DRIVER, 'PYTHONDONTWRITEBYTECODE': '1'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inner(expected):
    # Reuse the existing read-only canary/socket operations; no bootstrap entry.
    from probe import cannot_connect, rejected
    marker = Path('/protected-canary/marker.txt'); before = marker.read_bytes()
    checks = {'private_work_write': False}
    Path('/work/write-canary').write_text('private GPU prerequisite\n')
    checks['private_work_write'] = Path('/work/write-canary').read_text() == 'private GPU prerequisite\n'
    checks['protected_write_denied'] = rejected(lambda: marker.write_text('changed'))
    checks['protected_delete_denied'] = rejected(marker.unlink)
    Path('/work/escape').symlink_to(marker)
    checks['protected_symlink_write_denied'] = rejected(lambda: Path('/work/escape').write_text('changed'))
    Path('/work/escape').unlink()
    checks['protected_canary_unchanged'] = marker.read_bytes() == before
    namespaces = {k: os.readlink('/proc/self/ns/' + k) for k in expected['namespaces']}
    checks['namespaces_separate'] = all(namespaces[k] != v for k, v in expected['namespaces'].items())
    checks['host_protected_paths_absent'] = all(not Path(p).exists() for p in expected['protected_roots'])
    checks['no_game_proton_steam_view'] = all(not Path(p).exists() for p in ['/game', '/proton', '/steam', '/compatdata'])
    checks['no_display_input_audio_view'] = all(not Path(p).exists() for p in
        ['/tmp/.X11-unix', '/dev/dri', '/dev/input', '/dev/snd', '/dev/nvidia-modeset'])
    checks['fixed_gpu_devices_only'] = (set(str(p) for p in Path('/dev').glob('nvidia*')) == set(DEVICES)
        and all(stat.S_ISCHR(Path(p).stat().st_mode) and
            [os.major(Path(p).stat().st_rdev), os.minor(Path(p).stat().st_rdev)] == expected['devices'][p]
            for p in DEVICES))
    mounts = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split(); mounts[fields[4]] = fields[5].split(',')
    checks['system_hardware_source_views_readonly'] = all('ro' in mounts.get(p, []) for p in
        ['/usr', '/lib', '/lib64', '/bin', '/sys', '/gpu-probe', '/gpu_probe.py', '/probe.py', '/protected-canary'])
    checks['nested_sys_mounts_readonly'] = all('ro' in flags for p, flags in mounts.items() if p.startswith('/sys/'))
    checks['environment_exact'] = dict(os.environ) == dict(ENVIRONMENT, PWD='/work')
    checks['private_working_directory'] = os.getcwd() == '/work'
    checks['private_run_only'] = sorted(p.name for p in Path('/run').iterdir()) == ['test']
    checks['capabilities_dropped'] = next(l for l in Path('/proc/self/status').read_text().splitlines()
        if l.startswith('CapEff:')).split()[1] == '0000000000000000'
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.unshare(0x10000000); error = ctypes.get_errno()
    checks['nested_user_namespace_denied'] = result == -1 and error in (errno.EPERM, errno.ENOSPC)
    for name, family, address in [
        ('filesystem_socket', socket.AF_UNIX, expected['filesystem_socket']),
        ('abstract_socket', socket.AF_UNIX, '\0' + expected['abstract_socket']),
        ('loopback_socket', socket.AF_INET, ('127.0.0.1', expected['tcp_port'])),
        ('outbound_test_net', socket.AF_INET, ('192.0.2.1', 9))]:
        checks[name + '_denied'] = cannot_connect(family, address)['denied']
    checks['only_private_loopback'] = [l.split(':')[0].strip() for l in
        Path('/proc/net/dev').read_text().splitlines()[2:]] == ['lo']
    checks['no_default_route'] = len(Path('/proc/net/route').read_text().splitlines()) == 1
    report = {'checks': checks, 'namespaces': namespaces, 'mounts': mounts, 'gpu': None}
    if not all(checks.values()):
        print(json.dumps(report)); return 1
    # Fixed binary, no parameters, private environment, all pipes explicitly owned.
    native = subprocess.run(['/gpu-probe'], stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=10, close_fds=True, env=os.environ.copy())
    report['gpu'] = {'exit': native.returncode, 'stderr': native.stderr,
                     'stdout': json.loads(native.stdout) if native.returncode == 0 else native.stdout}
    children = Path('/proc/self/task/' + str(os.getpid()) + '/children').read_text().strip()
    report['native_child_reaped'] = not children
    print(json.dumps(report))
    return 0 if native.returncode == 0 and not children else 1


def main():
    if len(sys.argv) != 1:
        raise ValueError('No configurable command, mount, environment or profile argument is allowed')
    repository = Path(__file__).resolve().parents[2]
    parent = repository / '.local/r04/gpu'
    from .isolation import owned_ancestors
    owned_ancestors(parent)
    context = json.loads((repository / '.local/r04/context.json').read_text())
    root = parent / uuid.uuid4().hex; root.mkdir(mode=0o700)
    for p in ['work', 'work/home', 'work/cache', 'protected']:
        (root / p).mkdir(mode=0o700)
    marker = root / 'protected/marker.txt'; marker.write_text('private GPU protected canary\n')
    binary = root / 'gpu-probe'; source = Path(__file__).with_suffix('.c')
    compile_command = ['/usr/bin/gcc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
        str(source), '-o', str(binary), '-lvulkan']
    compiler_environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8',
                            'HOME': str(root / 'work/home'), 'TMPDIR': str(root / 'work')}
    compiled = subprocess.run(compile_command, capture_output=True, text=True,
                              env=compiler_environment, timeout=30, stdin=subprocess.DEVNULL)
    (root / 'compile.json').write_text(json.dumps({'command': compile_command,
        'exit': compiled.returncode, 'stdout': compiled.stdout, 'stderr': compiled.stderr}, indent=2)+'\n')
    if compiled.returncode:
        raise RuntimeError('Native compilation failed; inspect private compile.json')
    devices = {}
    for p in DEVICES:
        s = Path(p).lstat()
        if not stat.S_ISCHR(s.st_mode): raise ValueError('Expected fixed NVIDIA character device')
        devices[p] = [os.major(s.st_rdev), os.minor(s.st_rdev)]
    if not Path(DRIVER).is_file() or Path(DRIVER).is_symlink():
        raise ValueError('Expected fixed installed NVIDIA ICD file')
    sockets = []
    process = None
    try:
        fs = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sockets.append(fs)
        fs_path = root / 'socket'; fs.bind(str(fs_path)); fs.listen(1)
        abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sockets.append(abstract)
        name = 'stellar-native-gpu-' + root.name; abstract.bind('\0' + name); abstract.listen(1)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM); sockets.append(tcp)
        tcp.bind(('127.0.0.1', 0)); tcp.listen(1)
        expected = {'devices': devices, 'protected_roots': context['protected_roots'],
            'namespaces': {k: os.readlink('/proc/self/ns/' + k) for k in ['mnt', 'net', 'ipc', 'pid', 'user']},
            'filesystem_socket': str(fs_path), 'abstract_socket': name, 'tcp_port': tcp.getsockname()[1]}
        args = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--disable-userns', '--new-session',
                '--die-with-parent', '--cap-drop', 'ALL', '--clearenv']
        for p in ['/usr', '/lib', '/lib64', '/bin', '/sys', '/etc/ld.so.cache']:
            args += ['--ro-bind', str(Path(p).resolve(strict=True)), p]
        args += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run',
                 '--dir', '/run/test', '--bind', str(root / 'work'), '/work',
                 '--ro-bind', str(root / 'protected'), '/protected-canary',
                 '--ro-bind', str(binary), '/gpu-probe',
                 '--ro-bind', str(Path(__file__).resolve()), '/gpu_probe.py',
                 '--ro-bind', str(Path(__file__).with_name('probe.py').resolve()), '/probe.py']
        for p in DEVICES: args += ['--dev-bind', p, p]
        for k, v in ENVIRONMENT.items(): args += ['--setenv', k, v]
        args += ['--chdir', '/work', '--remount-ro', '/', '--', '/usr/bin/python3', '-B',
                 '/gpu_probe.py', '--inner', json.dumps(expected)]
        registration = {'command': args, 'source_sha256': sha(source), 'launcher_sha256': sha(Path(__file__)),
                        'binary_sha256': sha(binary), 'driver_icd_sha256': sha(Path(DRIVER)), 'devices': devices}
        (root / 'inputs.json').write_text(json.dumps(registration, indent=2)+'\n')
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'}, start_new_session=True, close_fds=True)
        stdout, stderr = process.communicate(timeout=20)
        report = {'exit': process.returncode, 'stdout': json.loads(stdout) if stdout.strip() else None,
                  'stderr': stderr, 'owned_parent_pid': process.pid, 'owned_parent_reaped': process.poll() is not None,
                  'canary_unchanged': marker.read_text() == 'private GPU protected canary\n'}
        result = root / 'result.json'; result.write_text(json.dumps(report, indent=2)+'\n')
        data = root / 'work/readback.rgba'
        exact = data.is_file() and not data.is_symlink() and data.read_bytes() == bytes([16, 32, 64, 255]) * 1024
        summary = {'profile': str(root), 'exit': report['exit'], 'exact_host_readback': exact,
                   'result_sha256': sha(result), 'boundary_checks_passed': sum(report['stdout']['checks'].values())
                   if report['stdout'] else 0}
        (root / 'host-checks.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps(summary))
        return 0 if report['exit'] == 0 and exact and report['canary_unchanged'] and report['owned_parent_reaped'] else 1
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        for s in sockets: s.close()
        (root / 'socket').unlink(missing_ok=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--inner':
        raise SystemExit(inner(json.loads(sys.argv[2])))
    raise SystemExit(main())
