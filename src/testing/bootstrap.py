"""Prepare or execute the exact reviewed R04-b fixed prefix bootstrap event."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import uuid

from .isolation import IsolationRefused, command, construct, digest, owned_ancestors, sources_from_context

CONTRACT = 'stellar-mod-console/R04-b/v2'
ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'HOME': '/home/test', 'LC_ALL': 'C.UTF-8', 'TMPDIR': '/tmp',
               'XDG_RUNTIME_DIR': '/run/test', 'PYTHONDONTWRITEBYTECODE': '1',
               'STEAM_COMPAT_DATA_PATH': '/work/compatdata',
               'STEAM_COMPAT_CLIENT_INSTALL_PATH': '/work/fake-steam',
               'PROTON_USE_WINED3D': '1', 'PROTON_DISABLE_NVAPI': '1', 'WINEDEBUG': '-all'}
MASKS = ['/usr/lib/x86_64-linux-gnu/nvidia/wine', '/lib/x86_64-linux-gnu/nvidia/wine', '/proton/contrib']


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def nonfollowing_tree(root: Path):
    """Never resolve a Wine Z: or any other prefix symlink on the host."""
    records = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            st = path.lstat()
            item = {'relative': str(path.relative_to(root)), 'size': st.st_size}
            if stat.S_ISLNK(st.st_mode):
                item.update(type='symlink', target=os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                item['type'] = 'directory'
            elif stat.S_ISREG(st.st_mode):
                item.update(type='file', sha256=digest(path))
            else:
                item['type'] = 'special'
            records.append(item)
    return sorted(records, key=lambda x: x['relative'])


def verify_runtime(preflight):
    root = Path(preflight['runtime_root'])
    actual_files, actual_links = {}, {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        if '__pycache__' in dirs:
            dirs.remove('__pycache__')
        for name in dirs + files:
            p = Path(directory) / name
            st = p.lstat(); rel = str(p.relative_to(root))
            if stat.S_ISLNK(st.st_mode):
                actual_links[rel] = os.readlink(p)
            elif stat.S_ISREG(st.st_mode):
                actual_files[rel] = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
    wanted = {x['relative']: x for x in preflight['regular_files']}
    if set(actual_files) != set(wanted) or actual_links != {x['relative']: x['target'] for x in preflight['runtime_symlinks']}:
        raise IsolationRefused('Proton runtime entry set or symlink identity changed')
    for rel, entry in wanted.items():
        if actual_files[rel] != entry['stamp'] or digest(root / rel) != entry['sha256']:
            raise IsolationRefused('Proton runtime identity changed')
    if preflight['fixup_marker'] != preflight['fixup_source_mtime'] or preflight['user_settings_exists'] or preflight['legacy_dist_exists']:
        raise IsolationRefused('Runtime needs fixup or has unreviewed user settings; no repair fallback')


def protected_snapshot(reference):
    """Read exact protected preimages; never write or inspect credential values."""
    result = {'hashed': [], 'game_tree_metadata': [], 'credential_values_read': False}
    for field in ['hashed', 'game_tree_metadata']:
        for entry in reference[field]:
            path = Path(entry['path']); st = path.lstat()
            item = {'path': str(path), 'stamp': [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]}
            if field == 'hashed':
                item['sha256'] = digest(path)
            else:
                item['symlink'] = os.readlink(path) if path.is_symlink() else None
            result[field].append(item)
    return result


def prepare(private: Path, context, preflight):
    verify_runtime(preflight)
    sources = sources_from_context(context)
    if str(sources['proton']) != preflight['runtime_root']:
        raise IsolationRefused('Context runtime differs from frozen preflight')
    root = construct(private / 'profiles', [Path(p) for p in context['protected_roots']], sources, uuid.uuid4().hex)
    for rel in ['control', 'work/fake-steam', 'work/empty-mask', 'work/logs', 'work/notices']:
        (root / rel).mkdir(mode=0o700)
    (root / 'work/proton-dist.lock').write_bytes(b'')
    (root / 'control/passwd').write_text(f'test:x:{os.getuid()}:{os.getgid()}:Private Wine test:/home/test:/bin/sh\n')
    (root / 'control/group').write_text(f'test:x:{os.getgid()}:\n')
    for name in ['LICENSE', 'LICENSE.OFL', 'PATENTS.AV1']:
        # This private copy is explicitly licensed notice preservation, not publication.
        (root / 'work/notices' / name).write_bytes((sources['proton'] / name).read_bytes())
    plan = {'contract': CONTRACT, 'profile': str(root), 'context': context,
            'environment': ENVIRONMENT, 'masks': MASKS,
            'source_preflight_sha256': json_digest(preflight), 'private_lock': 'work/proton-dist.lock',
            'fresh_tree': nonfollowing_tree(root), 'runtime': 'NOT_RUN', 'launch': 'REQUIRES_EXACT_GATE'}
    (root / 'bootstrap-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    return root


def validate_fresh(root: Path, preflight):
    owned_ancestors(root)
    plan = json.loads((root / 'bootstrap-plan.json').read_text())
    if plan['contract'] != CONTRACT or plan['profile'] != str(root) or plan['environment'] != ENVIRONMENT or plan['masks'] != MASKS:
        raise IsolationRefused('Fixed plan changed or extra environment/mount selected')
    if plan['source_preflight_sha256'] != json_digest(preflight):
        raise IsolationRefused('Frozen source preflight changed')
    now = [x for x in nonfollowing_tree(root) if x['relative'] != 'bootstrap-plan.json']
    # Dry evidence/control metadata lives outside work and is registered separately.
    original = plan['fresh_tree']
    for item in original:
        if item not in now:
            raise IsolationRefused('Fresh private profile changed or prefix reused')
    allowed_extra = {'dry-result.json', 'work/probe-write.txt'}
    if any(x['relative'] not in {y['relative'] for y in original} | allowed_extra for x in now):
        raise IsolationRefused('Fresh profile has unregistered extra content')
    if any((root / 'work/compatdata').iterdir()):
        raise IsolationRefused('Prefix already exists; no reuse or blind retry')
    verify_runtime(preflight)
    return plan


def namespace_command(root: Path, request):
    plan = json.loads((root / 'bootstrap-plan.json').read_text())
    args = command(root, sources_from_context(plan['context']), '{}')
    args = args[:args.index('--')]
    game_mount = ['--ro-bind', plan['context']['readonly_sources']['game'], '/game']
    position = next(i for i in range(len(args)) if args[i:i+3] == game_mount)
    del args[position:position+3]
    assert args[-2:] == ['--remount-ro', '/']
    del args[-2:]
    args += ['--bind', str(root / 'work/proton-dist.lock'), '/proton/dist.lock',
             '--bind', str(root / 'work/home'), '/home/test', '--tmpfs', '/dev/shm',
             '--ro-bind', str(root / 'control/passwd'), '/etc/passwd',
             '--ro-bind', str(root / 'control/group'), '/etc/group']
    for target in MASKS:
        args += ['--ro-bind', str(root / 'work/empty-mask'), target]
    args += ['--ro-bind', str(Path(__file__).with_name('bootstrap_driver.py')), '/bootstrap-driver.py']
    for key, value in ENVIRONMENT.items():
        args += ['--setenv', key, value]
    args += ['--remount-ro', '/', '--', '/usr/bin/python3', '-B', '/bootstrap-driver.py', json.dumps(request)]
    return args


def run_mode(root: Path, mode: str, preflight):
    if mode not in ['dry', 'execute']:
        raise IsolationRefused('No arbitrary command mode')
    plan = validate_fresh(root, preflight)
    if mode == 'execute':
        gate_path = root.parents[1] / 'bootstrap-launch-registration.json'
        gate = json.loads(gate_path.read_text())
        if gate.get('approval') != 'APPROVE_LAUNCH' or gate['profile'] != str(root):
            raise IsolationRefused('No exact launch approval')
        for path, sha in gate['files'].items():
            if digest(Path(path)) != sha:
                raise IsolationRefused('Approved launch candidate changed')
        before = json.loads(Path(gate['protected_preimages']).read_text())
        if protected_snapshot(before) != before:
            raise IsolationRefused('Protected preimages changed before launch')
    sockets = []; fs = root / 'socket'
    try:
        for family, address in [(socket.AF_UNIX, str(fs)), (socket.AF_UNIX, '\0stellar-' + root.name),
                                (socket.AF_INET, ('127.0.0.1', 0))]:
            s = socket.socket(family, socket.SOCK_STREAM); sockets.append(s); s.bind(address); s.listen(1)
        expected = {'host_namespaces': {k: os.readlink('/proc/self/ns/' + k) for k in ['mnt','net','ipc','pid','user']},
                    'filesystem_socket': str(fs), 'abstract_socket': 'stellar-' + root.name,
                    'tcp_port': sockets[-1].getsockname()[1], 'protected_host_paths': plan['context']['protected_roots'],
                    'readonly_mounts': ['/proton'], 'allowed_environment': dict(ENVIRONMENT, PWD='/work'), 'private_shm': True}
        # Socket is outside the writable subtree. Remove it before fresh validation;
        # namespace_command only checks owned inode/type, not the fresh content list.
        args = namespace_command(root, {'mode': mode, 'expected': expected})
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'}, close_fds=True)
        ticks = Path(f'/proc/{child.pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        process = {'host_namespace_pid': child.pid, 'start_ticks': ticks, 'command_sha256': json_digest(args)}
        if mode == 'execute':
            (root / 'process.json').write_text(json.dumps(process) + '\n')
        try:
            stdout, stderr = child.communicate(timeout=75)
        except subprocess.TimeoutExpired:
            child.kill(); stdout, stderr = child.communicate()
            process['timeout'] = True
        try:
            report = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            report = {'unparsed_stdout': stdout}
        result = {'exit': child.returncode, 'stdout': report,
                  'stderr': stderr, 'command': args, 'process': dict(process, reaped=child.poll() is not None)}
        (root / ('dry-result.json' if mode == 'dry' else 'execution-result.json')).write_text(json.dumps(result, indent=2) + '\n')
        if result['exit'] != 0:
            raise IsolationRefused('Fixed namespace step failed; evidence retained; no blind retry')
        return result
    finally:
        for s in sockets:s.close()
        fs.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare','dry','execute'])
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    private = Path(__file__).resolve().parents[2] / '.local/r04'
    preflight = json.loads((private / 'bootstrap-preflight.json').read_text())
    if args.mode == 'prepare':
        if args.profile is not None:raise IsolationRefused('Preparation always creates a fresh target')
        root = prepare(private, json.loads((private / 'context.json').read_text()), preflight)
        print(json.dumps({'profile':str(root),'runtime':'NOT_RUN'})); return
    if args.profile is None or args.profile.parent != private / 'profiles':
        raise IsolationRefused('Exact private profile required')
    result = run_mode(args.profile, args.mode, preflight)
    print(json.dumps({'profile':str(args.profile),'exit':result['exit'],'mode':args.mode}))


if __name__ == '__main__':
    main()
