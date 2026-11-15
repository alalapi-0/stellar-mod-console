"""Fixed disposable negative probe, executed only inside the R04-a namespace."""

import errno
import ctypes
import json
import os
from pathlib import Path
import socket
import sys


def rejected(operation):
    try:
        operation()
    except OSError as error:
        return error.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
    return False


def cannot_connect(family, address):
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        try:
            sock.connect(address)
        except OSError as error:
            return {'denied': True, 'errno': error.errno}
    return {'denied': False}


def inspect(expected):
    checks = {}
    work = Path('/work')
    marker = Path('/protected-canary/marker.txt')
    original = marker.read_bytes()
    (work / 'probe-write.txt').write_text('owned write succeeded\n')
    checks['owned_write'] = (work / 'probe-write.txt').read_text() == 'owned write succeeded\n'
    checks['protected_create_denied'] = rejected(lambda: Path('/protected-canary/new').write_text('x'))
    checks['protected_modify_denied'] = rejected(lambda: marker.write_bytes(b'changed'))
    checks['protected_delete_denied'] = rejected(marker.unlink)
    checks['protected_rename_denied'] = rejected(lambda: marker.rename('/protected-canary/renamed'))
    (work / 'escape-link').symlink_to(marker)
    checks['symlink_write_denied'] = rejected(lambda: (work / 'escape-link').write_bytes(b'changed'))
    (work / 'escape-link').unlink()
    checks['protected_canary_unchanged'] = original == marker.read_bytes()
    mounts = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        mounts[fields[4]] = fields[5].split(',')
    checks['actual_sources_readonly'] = all('ro' in mounts.get(p, []) for p in expected.get('readonly_mounts', ['/game', '/proton']))
    checks['protected_host_paths_absent'] = all(not Path(p).exists() for p in expected['protected_host_paths'])
    namespaces = {k: os.readlink('/proc/self/ns/' + k) for k in expected['host_namespaces']}
    checks['namespaces_separate'] = all(namespaces[k] != v for k, v in expected['host_namespaces'].items())
    checks['capabilities_dropped'] = next(l for l in Path('/proc/self/status').read_text().splitlines()
                                           if l.startswith('CapEff:')).split()[1] == '0000000000000000'
    libc = ctypes.CDLL(None, use_errno=True)
    nested_result = libc.unshare(0x10000000)
    nested_errno = ctypes.get_errno()
    # --disable-userns sets a namespace-local limit: Linux reports ENOSPC here.
    checks['nested_user_namespace_denied'] = nested_result == -1 and nested_errno in (errno.EPERM, errno.ENOSPC)
    connections = {
        'host_filesystem_socket': cannot_connect(socket.AF_UNIX, expected['filesystem_socket']),
        'host_abstract_socket': cannot_connect(socket.AF_UNIX, '\0' + expected['abstract_socket']),
        'host_loopback_socket': cannot_connect(socket.AF_INET, ('127.0.0.1', expected['tcp_port'])),
        'outbound_test_net': cannot_connect(socket.AF_INET, ('192.0.2.1', 9)),
    }
    for name, result in connections.items():
        checks[name + '_denied'] = result['denied']
    checks['network_no_external_interface'] = set(Path('/sys/class/net').glob('*')) == set()
    # /sys is deliberately absent. Procfs is private and records only the isolated lo device.
    interfaces = [l.split(':')[0].strip() for l in Path('/proc/net/dev').read_text().splitlines()[2:]]
    checks['network_only_loopback'] = interfaces == ['lo']
    checks['network_no_default_route'] = len(Path('/proc/net/route').read_text().splitlines()) == 1
    blocked = ['SteamAppId', 'SteamGameId', 'STEAM_COMPAT_DATA_PATH', 'STEAM_COMPAT_CLIENT_INSTALL_PATH',
               'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER',
               'LD_PRELOAD', 'LD_LIBRARY_PATH', 'WINEPREFIX', 'SteamVirtualGamepadInfo']
    allowed_environment = expected.get('allowed_environment', {})
    checks['host_environment_absent'] = not any(k in os.environ for k in blocked if k not in allowed_environment)
    # bubblewrap sets PWD to its private --chdir value after clearing the host environment.
    checks['environment_allowlist'] = (dict(os.environ) == allowed_environment if allowed_environment else
        set(os.environ) <= {'PATH', 'HOME', 'LC_ALL', 'TMPDIR', 'XDG_RUNTIME_DIR', 'PWD'})
    checks['working_directory_private'] = os.getcwd() == '/work' and os.environ.get('PWD') == '/work'
    checks['host_runtime_absent'] = sorted(p.name for p in Path('/run').iterdir()) == ['test']
    checks['host_shared_memory_absent'] = ('/dev/shm' in mounts if expected.get('private_shm') else
        not Path('/dev/shm').exists() or not list(Path('/dev/shm').iterdir()))
    checks['display_devices_absent'] = all(not Path(p).exists() for p in
        ['/tmp/.X11-unix', '/dev/dri', '/dev/nvidia0', '/dev/nvidiactl', '/dev/input', '/dev/snd', '/sys'])
    if expected.get('prefix_empty', True):
        checks['prefix_and_profile_empty'] = all(not list(Path('/work/' + p).iterdir()) for p in ['compatdata', 'profile'])
    return {'checks': checks, 'connections': connections,
                      'nested_user_namespace': {'result': nested_result, 'errno': nested_errno},
                      'namespaces': namespaces, 'mounts': mounts,
                      'runtime': 'Namespace inspection only'}


def main():
    report = inspect(json.loads(sys.argv[1]))
    print(json.dumps(report, sort_keys=True))
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
