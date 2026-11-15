"""Fixed self-authored PE experiment in a new private headless GPU namespace.

No arbitrary application, profile reuse, Steam/game view or environment override.
Historical R04-b launch registration and execution entry are never used here.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

DEVICES = ('/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm')
DRIVER = '/usr/share/vulkan/icd.d/nvidia_icd.json'
IMPORTS = ('GetStdHandle', 'WriteFile', 'ExitProcess', 'LoadLibraryExA', 'GetProcAddress',
           'GetLastError', 'CreateEventA', 'WaitForSingleObject', 'CloseHandle', 'CreateFileA')
MASKS = ('/usr/lib/x86_64-linux-gnu/nvidia/wine', '/lib/x86_64-linux-gnu/nvidia/wine', '/proton/contrib')
ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'HOME': '/home/test', 'LC_ALL': 'C.UTF-8',
    'TMPDIR': '/tmp', 'XDG_RUNTIME_DIR': '/run/test', 'XDG_CACHE_HOME': '/work/cache',
    'PYTHONDONTWRITEBYTECODE': '1', 'VK_DRIVER_FILES': DRIVER,
    'STEAM_COMPAT_DATA_PATH': '/work/compatdata',
    'STEAM_COMPAT_CLIENT_INSTALL_PATH': '/work/fake-steam',
    'PROTON_DISABLE_NVAPI': '1', 'WINEDEBUG': '-all,+loaddll,+vulkan',
    'WINEDLLOVERRIDES': 'dxgi,d3d12,d3d12core=n',
    'DXVK_LOG_LEVEL': 'info', 'DXVK_LOG_PATH': '/work/logs', 'VKD3D_DEBUG': 'warn'}
ENVIRONMENT.update(DISPLAY=':99', XAUTHORITY='/control/Xauthority', DXVK_CONFIG_FILE='/control/dxvk.conf')
DISPLAY_COMMAND = ('/virtual-xserver', ':99', '-screen', '0', '640x480x24',
                   '-nolisten', 'tcp', '-auth', '/control/Xauthority', '-noreset', '-fp', 'built-ins',
                   '-extension', 'GLX')
COMMANDS = (
    ('/usr/bin/python3', '-B', '/proton/proton', 'getcompatpath', '/work'),
    ('/usr/bin/python3', '-B', '/proton/proton', 'runinprefix', 'Z:\\probe.exe'),
    ('/proton/files/bin/wineserver', '-w'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def create_test_authority(path):
    """New provider-format test credential; never import or print host credentials."""
    fields = [b'', b'99', b'MIT-MAGIC-COOKIE-1', os.urandom(16)]
    record = struct.pack('>H', 65535) + b''.join(struct.pack('>H', len(v)) + v for v in fields)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream: stream.write(record)


def start_private_display(report):
    log = open('/work/logs/xvfb.log', 'w')
    child = subprocess.Popen(DISPLAY_COMMAND, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        close_fds=True, env=dict(os.environ), start_new_session=True)
    log.close()
    report['command'] = DISPLAY_COMMAND; report['namespace_pid'] = child.pid
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and child.poll() is None:
            if Path('/tmp/.X11-unix/X99').exists():
                ready = subprocess.run(['/usr/bin/xdpyinfo', '-display', ':99'],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1)
                if ready.returncode == 0: break
            time.sleep(0.05)
        else: raise RuntimeError('Private display did not become ready within5seconds')
        unauthorized = subprocess.run(['/usr/bin/xdpyinfo', '-display', ':99'],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=2,
            env=dict(os.environ, XAUTHORITY='/tmp/no-authority'))
        report['checks'] = {'authorized_client_ready': ready.returncode == 0,
            'fixed_screen': '640x480 pixels' in ready.stdout, 'server_alive': child.poll() is None,
            'unauthorized_client_denied': unauthorized.returncode != 0,
            'no_tcp_listener': len(Path('/proc/net/tcp').read_text().splitlines()) == 1 and
                               len(Path('/proc/net/tcp6').read_text().splitlines()) == 1}
        if not all(report['checks'].values()): raise RuntimeError('Private display prerequisite failed')
        return child
    except Exception:
        stop_private_display(child, report)
        raise


def stop_private_display(child, report):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
    report['reaped'] = child.poll() is not None
    report['exit'] = child.returncode


def collect_owned_child(child, timeout):
    """Caller creates this child in a new session; end its group on timeout.

    Wine descendants can inherit output pipes after their Python parent exits.
    Keep partial diagnostics even when namespace teardown must close those pipes.
    """
    try:
        stdout, stderr = child.communicate(timeout=timeout)
        return {'exit': child.returncode, 'stdout': stdout, 'stderr': stderr}
    except subprocess.TimeoutExpired as error:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        try:
            stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait(timeout=5)
            stdout, stderr = error.stdout or b'', error.stderr or b''
            child.stdout.close(); child.stderr.close()
        return {'exit': child.returncode, 'stdout': stdout.decode(errors='replace') if isinstance(stdout, bytes) else stdout,
                'stderr': stderr.decode(errors='replace') if isinstance(stderr, bytes) else stderr, 'timeout': True}


def validate_image(path):
    from .runtime_probe import inspect_file
    image = inspect_file(path)
    entries = image['imports']
    if (image['bits'] != 64 or image['machine'] != '0x8664' or image['delay_imports'] or
            len(entries) != 1 or entries[0]['dll'].lower() != 'kernel32.dll' or
            len(entries[0]['symbols']) != len(IMPORTS) or
            any(set(s) != {'name'} for s in entries[0]['symbols']) or
            {s['name'] for s in entries[0]['symbols']} != set(IMPORTS)):
        raise ValueError('Unexpected PE architecture or imports')
    data = path.read_bytes(); pe = struct.unpack_from('<I', data, 0x3c)[0]
    subsystem, characteristics = struct.unpack_from('<HH', data, pe + 24 + 68)
    if subsystem != 3 or characteristics & 0x140 != 0x140:
        raise ValueError('Expected console PE with NX and dynamic base')
    return image


def build(root):
    directory = root / 'build'; directory.mkdir(mode=0o700)
    source = Path(__file__).with_suffix('.c')
    stub = directory / 'stub.c'
    stub.write_text('\n'.join('__attribute__((ms_abi)) void ' + n + '(void){}' for n in IMPORTS) + '\n')
    definition = directory / 'kernel32.def'
    definition.write_text('LIBRARY KERNEL32.dll\nEXPORTS\n' + '\n'.join(IMPORTS) + '\n')
    flags = ['-std=c11', '-Os', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-fno-pic', '-fno-pie', '-mcmodel=large', '-maccumulate-outgoing-args', '-fno-ident',
        '-fno-asynchronous-unwind-tables', '-fno-stack-protector', '-mno-red-zone']
    dll = directory / 'import-stub.dll'; binary = root / 'probe.exe'
    commands = [
        ['/usr/bin/gcc', *flags, '-c', str(stub), '-o', str(directory / 'stub.elf')],
        ['/usr/bin/objcopy', '-O', 'pe-x86-64', str(directory / 'stub.elf'), str(directory / 'stub.obj')],
        ['/usr/bin/ld', '-mi386pep', '--shared', '--no-insert-timestamp', '--entry', 'ExitProcess',
         '--out-implib', str(directory / 'kernel32.a'), '-o', str(dll),
         str(directory / 'stub.obj'), str(definition)],
        ['/usr/bin/gcc', *flags, '-c', str(source), '-o', str(directory / 'probe.elf')],
        ['/usr/bin/ld', '-mi386pep', '--no-insert-timestamp', '--subsystem', 'console', '--entry', 'entry',
         '--image-base', '0x140000000', '--nxcompat', '--dynamicbase', '-o', str(binary),
         str(directory / 'probe.elf'), str(directory / 'kernel32.a')]]
    results = []
    environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': str(root / 'work/home'),
                   'TMPDIR': str(directory)}
    try:
        for args in commands:
            p = subprocess.run(args, capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL, env=environment, close_fds=True)
            results.append({'command': args, 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
            if p.returncode:
                raise RuntimeError('Owned compilation failed; see compile.json')
        image = validate_image(binary)
    finally:
        # The self-authored fake DLL only produces an import library; never run/map it.
        dll.unlink(missing_ok=True)
        save(root / 'compile.json', results)
    return {'source_sha256': sha(source), 'launcher_sha256': sha(Path(__file__)),
            'binary_sha256': sha(binary), 'pe': image, 'stub_dll_removed': not dll.exists()}


def boundary(expected, display_running=False):
    from probe import cannot_connect, rejected
    marker = Path('/protected-canary/marker.txt'); before = marker.read_bytes()
    Path('/work/write-canary').write_text('fixed Windows prerequisite\n')
    checks = {'private_write': Path('/work/write-canary').read_text() == 'fixed Windows prerequisite\n',
        'protected_write_denied': rejected(lambda: marker.write_text('changed')),
        'protected_delete_denied': rejected(marker.unlink)}
    Path('/work/escape').symlink_to(marker)
    checks['symlink_escape_denied'] = rejected(lambda: Path('/work/escape').write_text('changed'))
    Path('/work/escape').unlink()
    checks['canary_unchanged'] = marker.read_bytes() == before
    namespaces = {k: os.readlink('/proc/self/ns/' + k) for k in expected['namespaces']}
    checks['namespaces_separate'] = all(namespaces[k] != v for k, v in expected['namespaces'].items())
    checks['host_protected_paths_absent'] = all(not Path(p).exists() for p in expected['protected_roots'])
    checks['game_client_host_input_audio_absent'] = all(not Path(p).exists() for p in
        ['/game', '/steam', '/compatdata', '/run/user', '/dev/dri', '/dev/input', '/dev/snd', '/dev/nvidia-modeset'])
    sockets = Path('/tmp/.X11-unix')
    checks['only_expected_private_display'] = (sorted(p.name for p in sockets.iterdir()) if sockets.exists() else []) == (['X99'] if display_running else [])
    checks['fake_client_empty'] = not list(Path('/work/fake-steam').iterdir())
    checks['fixed_gpu_devices_only'] = set(str(p) for p in Path('/dev').glob('nvidia*')) == set(DEVICES)
    checks['device_identities'] = all(stat.S_ISCHR(Path(p).stat().st_mode) and
        [os.major(Path(p).stat().st_rdev), os.minor(Path(p).stat().st_rdev)] == expected['devices'][p] for p in DEVICES)
    mounts = {f[4]: f[5].split(',') for f in (l.split() for l in Path('/proc/self/mountinfo').read_text().splitlines())}
    checks['readonly_sources'] = all('ro' in mounts.get(p, []) for p in
        ['/usr', '/lib', '/lib64', '/bin', '/sys', '/proton', '/probe.exe', '/d3d12_probe.py', '/probe.py', '/protected-canary', '/virtual-xserver', '/control'])
    checks['readonly_nested_sys'] = all('ro' in v for p, v in mounts.items() if p.startswith('/sys/'))
    checks['fixed_masks'] = all('ro' in mounts.get(p, []) and not list(Path(p).iterdir()) for p in MASKS)
    checks['own_graphics_config_exact'] = Path('/control/dxvk.conf').read_text() == 'dxgi.hideNvidiaGpu = False\n'
    checks['private_lock'] = 'rw' in mounts.get('/proton/dist.lock', [])
    checks['private_shm'] = 'rw' in mounts.get('/dev/shm', [])
    checks['environment_exact'] = dict(os.environ) == dict(ENVIRONMENT, PWD='/work')
    checks['private_cwd'] = os.getcwd() == '/work'
    checks['private_run'] = sorted(p.name for p in Path('/run').iterdir()) == ['test']
    checks['caps_dropped'] = next(l for l in Path('/proc/self/status').read_text().splitlines()
        if l.startswith('CapEff:')).split()[1] == '0000000000000000'
    libc = ctypes.CDLL(None, use_errno=True)
    rc = libc.unshare(0x10000000); err = ctypes.get_errno()
    checks['nested_userns_denied'] = rc == -1 and err in (errno.EPERM, errno.ENOSPC)
    for name, family, address in [('filesystem', socket.AF_UNIX, expected['filesystem_socket']),
        ('abstract', socket.AF_UNIX, '\0' + expected['abstract_socket']),
        ('loopback', socket.AF_INET, ('127.0.0.1', expected['tcp_port'])),
        ('outbound', socket.AF_INET, ('192.0.2.1', 9))]:
        checks[name + '_socket_denied'] = cannot_connect(family, address)['denied']
    checks['only_loopback'] = [l.split(':')[0].strip() for l in Path('/proc/net/dev').read_text().splitlines()[2:]] == ['lo']
    checks['no_default_route'] = len(Path('/proc/net/route').read_text().splitlines()) == 1
    return {'checks': checks, 'namespaces': namespaces, 'mounts': mounts}


def inner(expected):
    report = {'before': boundary(expected), 'steps': [], 'passed': False}
    if not all(report['before']['checks'].values()):
        print(json.dumps(report)); return 1
    display = None
    try:
        if list(Path('/work/compatdata').iterdir()) or Path('/work/readback.rgba').exists():
            raise ValueError('No prefix/output reuse')
        report['display'] = {}
        display = start_private_display(report['display'])
        report['display_boundary'] = boundary(expected, display_running=True)
        if not all(report['display_boundary']['checks'].values()):
            raise RuntimeError('Boundary changed after private display startup')
        for args, timeout in zip(COMMANDS, [40, 20, 15]):
            env = dict(os.environ)
            if args == COMMANDS[-1]: env['WINEPREFIX'] = '/work/compatdata/pfx'
            step = {'command': args}; report['steps'].append(step)
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env, close_fds=True, start_new_session=True)
            step.update(collect_owned_child(child, timeout))
            if step.get('timeout'):
                raise RuntimeError('Fixed Windows step timeout; owned namespace teardown required')
            if child.returncode and args != COMMANDS[1]: raise RuntimeError('Fixed prerequisite failed')
        report['windows'] = json.loads(report['steps'][1]['stdout'])
        stop_private_display(display, report['display']); display = None
        report['remaining_namespace_pids'] = sorted(int(p.name) for p in Path('/proc').iterdir()
            if p.name.isdecimal() and int(p.name) not in [1, os.getpid()])
        report['after'] = boundary(expected)
        report['passed'] = (report['steps'][0]['stdout'].strip() == 'Z:\\work' and
            report['steps'][1]['exit'] == 0 and report['windows']['passed'] and
            not report['remaining_namespace_pids'] and all(report['after']['checks'].values()))
    except Exception as error:
        report['error'] = str(error)
    finally:
        if display is not None: stop_private_display(display, report['display'])
    print(json.dumps(report)); return 0 if report['passed'] else 1


def main():
    if len(sys.argv) != 1: raise ValueError('No configurable launch/profile/environment argument')
    from .isolation import owned_ancestors, disjoint
    from .bootstrap import verify_runtime, protected_snapshot, nonfollowing_tree
    repository = Path(__file__).resolve().parents[2]; private = repository / '.local/r04'
    parent = private / 'd3d12'; owned_ancestors(parent)
    context = json.loads((private / 'context.json').read_text())
    preflight = json.loads((private / 'bootstrap-preflight.json').read_text())
    virtual = json.loads((private / 'graphics/runtime.json').read_text())
    virtual_binary = Path(virtual['binary']); owned_ancestors(virtual_binary.parent)
    if (virtual_binary.is_symlink() or not virtual_binary.is_file() or
            virtual_binary.stat().st_uid != os.getuid() or
            sha(virtual_binary) != virtual['binary_sha256'] or sha(Path(virtual['notice'])) != virtual['notice_sha256']):
        raise ValueError('Fixed licensed private display runtime changed')
    runtime = Path(context['readonly_sources']['proton']).resolve(strict=True)
    if str(runtime) != preflight['runtime_root']: raise ValueError('Runtime differs from frozen preflight')
    verify_runtime(preflight)
    root = parent / uuid.uuid4().hex
    if any(not disjoint(root, Path(p).resolve(strict=True)) for p in context['protected_roots']):
        raise ValueError('Private profile overlaps protected path')
    root.mkdir(mode=0o700)
    for rel in ['control', 'protected', 'work', 'work/home', 'work/cache', 'work/compatdata',
                'work/fake-steam', 'work/empty-mask', 'work/notices', 'work/logs']:
        (root / rel).mkdir(mode=0o700)
    marker = root / 'protected/marker.txt'; marker.write_text('fixed Windows protected canary\n')
    (root / 'work/proton-dist.lock').write_bytes(b'')
    (root / 'control/passwd').write_text(f'test:x:{os.getuid()}:{os.getgid()}:Private test:/home/test:/bin/sh\n')
    (root / 'control/group').write_text(f'test:x:{os.getgid()}:\n')
    create_test_authority(root / 'control/Xauthority')
    (root / 'control/dxvk.conf').write_text('dxgi.hideNvidiaGpu = False\n')
    for name in ['LICENSE', 'LICENSE.OFL', 'PATENTS.AV1']:
        (root / 'work/notices' / name).write_bytes((runtime / name).read_bytes())
    reference = json.loads((private / 'gpu/47d28f1adf0f4483b326a81b63f9a1cf/protected-after.json').read_text())
    before = protected_snapshot(reference); save(root / 'protected-before.json', before)
    registration = build(root)
    devices = {}
    for p in DEVICES:
        st = Path(p).lstat()
        if not stat.S_ISCHR(st.st_mode): raise ValueError('Expected NVIDIA character device')
        devices[p] = [os.major(st.st_rdev), os.minor(st.st_rdev)]
    if not Path(DRIVER).is_file() or Path(DRIVER).is_symlink(): raise ValueError('Expected installed fixed NVIDIA ICD')
    sockets = []; process = None
    try:
        fs = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sockets.append(fs)
        fs.bind(str(root / 'socket')); fs.listen(1)
        abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sockets.append(abstract)
        abstract_name = 'stellar-d3d12-' + root.name; abstract.bind('\0' + abstract_name); abstract.listen(1)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM); sockets.append(tcp)
        tcp.bind(('127.0.0.1', 0)); tcp.listen(1)
        expected = {'devices': devices, 'protected_roots': context['protected_roots'],
            'namespaces': {k: os.readlink('/proc/self/ns/' + k) for k in ['mnt','net','ipc','pid','user']},
            'filesystem_socket': str(root / 'socket'), 'abstract_socket': abstract_name, 'tcp_port': tcp.getsockname()[1]}
        args = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--disable-userns', '--new-session',
            '--die-with-parent', '--cap-drop', 'ALL', '--clearenv']
        for p in ['/usr', '/lib', '/lib64', '/bin', '/sys', '/etc/ld.so.cache']:
            args += ['--ro-bind', str(Path(p).resolve(strict=True)), p]
        args += ['--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/tmp/.X11-unix',
            '--tmpfs','/run','--dir','/run/test',
            '--tmpfs','/dev/shm','--bind',str(root / 'work'),'/work',
            '--bind',str(root / 'work/home'),'/home/test','--ro-bind',str(runtime),'/proton',
            '--bind',str(root / 'work/proton-dist.lock'),'/proton/dist.lock',
            '--ro-bind',str(root / 'control/passwd'),'/etc/passwd',
            '--ro-bind',str(root / 'control/group'),'/etc/group',
            '--ro-bind',str(root / 'control'),'/control',
            '--ro-bind',str(virtual_binary),'/virtual-xserver',
            '--ro-bind',str(root / 'protected'),'/protected-canary',
            '--ro-bind',str(root / 'probe.exe'),'/probe.exe',
            '--ro-bind',str(Path(__file__).resolve()),'/d3d12_probe.py',
            '--ro-bind',str(Path(__file__).with_name('probe.py').resolve()),'/probe.py']
        for p in MASKS: args += ['--ro-bind',str(root / 'work/empty-mask'),p]
        for p in DEVICES: args += ['--dev-bind',p,p]
        for k,v in ENVIRONMENT.items(): args += ['--setenv',k,v]
        args += ['--chdir','/work','--remount-ro','/','--','/usr/bin/python3','-B',
                 '/d3d12_probe.py','--inner',json.dumps(expected)]
        registration.update(command=args,devices=devices,driver_icd_sha256=sha(Path(DRIVER)),
            virtual_display_binary_sha256=virtual['binary_sha256'],virtual_display_notice_sha256=virtual['notice_sha256'],
            runtime_manifest_sha256=sha(private / 'bootstrap-preflight.json'),protected_before_sha256=sha(root / 'protected-before.json'))
        save(root / 'inputs.json',registration)
        if protected_snapshot(before) != before: raise ValueError('Protected inputs changed before launch')
        process = subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},start_new_session=True,close_fds=True)
        try:
            stdout,stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL); stdout,stderr = process.communicate(timeout=5)
        report = {'exit':process.returncode,'stdout':json.loads(stdout) if stdout.strip() else None,
            'stderr':stderr,'owned_parent_pid':process.pid,'owned_parent_reaped':process.poll() is not None,
            'canary_unchanged':marker.read_text()=='fixed Windows protected canary\n'}
        save(root / 'result.json',report)
        after = protected_snapshot(before); save(root / 'protected-after.json',after)
        data = root / 'work/readback.rgba'
        exact = data.is_file() and not data.is_symlink() and data.read_bytes()==bytes([16,32,64,255])*1024
        save(root / 'outputs.json',nonfollowing_tree(root / 'work'))
        summary = {'profile':str(root),'exit':report['exit'],'exact_host_readback':exact,
            'protected_inputs_unchanged':before==after,'parent_reaped':report['owned_parent_reaped'],
            'canary_unchanged':report['canary_unchanged'],'result_sha256':sha(root / 'result.json')}
        save(root / 'host-checks.json',summary); print(json.dumps(summary))
        return 0 if report['exit']==0 and exact and before==after and report['canary_unchanged'] and report['owned_parent_reaped'] else 1
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL); process.communicate(timeout=5)
        for s in sockets: s.close()
        (root / 'socket').unlink(missing_ok=True)


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--inner': raise SystemExit(inner(json.loads(sys.argv[2])))
    raise SystemExit(main())
