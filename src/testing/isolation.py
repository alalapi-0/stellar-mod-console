"""R04-a construction and negative probes only; this module cannot launch apps.

No Steam session, real prefix or save is copied. Sources stay at their paths and
are exposed read-only in an otherwise empty mount namespace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import uuid


class IsolationRefused(ValueError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owned_ancestors(path: Path) -> None:
    """Reject links and a writable target whose existing parent is not ours."""
    if not path.is_absolute() or '..' in path.parts:
        raise IsolationRefused('Private target must be an absolute canonical path')
    for ancestor in reversed((path, *path.parents)):
        if ancestor.is_symlink():
            raise IsolationRefused('Private target has a symlink ancestor')
        if ancestor.exists() and not ancestor.is_dir():
            raise IsolationRefused('Private target ancestor is not a directory')
    nearest = next(p for p in (path, *path.parents) if p.exists())
    if nearest.stat().st_uid != os.getuid():
        raise IsolationRefused('Private target parent is not owned by this user')


def disjoint(a: Path, b: Path) -> bool:
    return a != b and a not in b.parents and b not in a.parents


def sources_from_context(context: dict) -> dict[str, Path]:
    sources = context['readonly_sources']
    if set(sources) != {'game', 'proton'}:
        raise IsolationRefused('Exactly the game and Proton source views are required')
    result = {}
    for name, raw in sources.items():
        path = Path(raw)
        if not path.is_absolute() or not path.is_dir():
            raise IsolationRefused('Read-only source must be an existing absolute directory')
        result[name] = path.resolve(strict=True)
    return result


def construct(parent: Path, protected: list[Path], sources: dict[str, Path], name: str) -> Path:
    if len(name) != 32 or any(c not in '0123456789abcdef' for c in name):
        raise IsolationRefused('Private profile ID must be a fresh UUID hex value')
    owned_ancestors(parent)
    root = parent / name
    protected = [p.resolve(strict=True) for p in protected]
    for p in [*protected, *sources.values()]:
        if not disjoint(root, p):
            raise IsolationRefused('Private root overlaps a protected source')
    if root.exists() or root.is_symlink():
        raise IsolationRefused('Private profile already exists; refusing to overwrite or reuse')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(mode=0o700)
    for rel in ['work', 'work/compatdata', 'work/profile', 'work/home', 'work/cache', 'protected']:
        (root / rel).mkdir(mode=0o700)
    (root / 'protected/marker.txt').write_text('disposable protected canary\n')
    manifest = {
        'schema_version': 1, 'id': name, 'root': str(root),
        'writable_host_root': str(root / 'work'),
        'protected_roots': [str(p) for p in protected],
        'readonly_sources': {k: str(v) for k, v in sources.items()},
        'configuration': 'EMPTY_SKELETON; source view read-only; no MOD selection or copy',
        'proton_prefix': 'EMPTY_SKELETON_NOT_INITIALIZED',
        'construction': {
            'directories': sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_dir()),
            'files': {'protected/marker.txt': digest(root / 'protected/marker.txt')},
            'copied_external_files': 0,
        },
        'launch': 'FORBIDDEN_UNDER_R04_A_V1',
    }
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return root


def command(root: Path, source_views: dict[str, Path], probe_arguments: str) -> list[str]:
    """Fixed probe command: no arbitrary command, extra mounts or environment."""
    bwrap = shutil.which('bwrap', path='/usr/bin:/bin')
    if bwrap is None:
        raise IsolationRefused('bubblewrap unavailable; no fallback')
    owned_ancestors(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest['root'] != str(root) or manifest['launch'] != 'FORBIDDEN_UNDER_R04_A_V1':
        raise IsolationRefused('Profile ownership manifest mismatch')
    for p in root.rglob('*'):
        if p.is_symlink():
            raise IsolationRefused('Private profile contains a symlink')
        if p.stat().st_uid != os.getuid() or (p.is_file() and p.stat().st_nlink != 1):
            raise IsolationRefused('Private profile contains a non-owned or shared-inode item')
    if {k: str(v) for k, v in source_views.items()} != manifest['readonly_sources']:
        raise IsolationRefused('Source views differ from construction manifest')
    for raw in manifest['protected_roots']:
        if not disjoint(root.resolve(strict=True), Path(raw).resolve(strict=True)):
            raise IsolationRefused('Private root overlaps protected data')
    args = [bwrap, '--unshare-all', '--unshare-user', '--disable-userns', '--new-session', '--die-with-parent',
            '--cap-drop', 'ALL', '--clearenv']
    # Distribution libraries only; no host home, Steam client, /tmp or /run bind.
    for raw in ['/usr', '/lib', '/lib64', '/bin']:
        path = Path(raw)
        if path.exists():
            args += ['--ro-bind', str(path.resolve(strict=True)), raw]
    if Path('/etc/ld.so.cache').is_file():
        args += ['--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache']
    args += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run',
             '--dir', '/run/test', '--dir', '/home/test',
             '--bind', str(root / 'work'), '/work',
             '--ro-bind', str(root / 'protected'), '/protected-canary']
    for name in ['game', 'proton']:
        args += ['--ro-bind', str(source_views[name]), '/' + name]
    args += ['--ro-bind', str(Path(__file__).with_name('probe.py').resolve()), '/probe.py',
             '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/home/test',
             '--setenv', 'LC_ALL', 'C.UTF-8', '--setenv', 'TMPDIR', '/tmp',
             '--setenv', 'XDG_RUNTIME_DIR', '/run/test',
             '--chdir', '/work', '--remount-ro', '/',
             '--', '/usr/bin/python3', '-B', '/probe.py', probe_arguments]
    return args


def run_probe(root: Path, source_views: dict[str, Path], protected: list[Path]) -> dict:
    """Own three disposable host listeners, then prove the namespace cannot reach them."""
    name = 'stellar-r04-' + root.name
    fs_path = root / 'socket'
    sockets = []
    marker = root / 'protected/marker.txt'
    before = digest(marker)
    host_ns = {k: os.readlink('/proc/self/ns/' + k) for k in ['mnt', 'net', 'ipc', 'pid', 'user']}
    try:
        filesystem = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sockets.append(filesystem)
        filesystem.bind(str(fs_path)); filesystem.listen(1)
        abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sockets.append(abstract)
        abstract.bind('\0' + name); abstract.listen(1)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockets.append(tcp)
        tcp.bind(('127.0.0.1', 0)); tcp.listen(1)
        arguments = json.dumps({'host_namespaces': host_ns, 'filesystem_socket': str(fs_path),
                                'abstract_socket': name, 'tcp_port': tcp.getsockname()[1],
                                'protected_host_paths': [str(p) for p in protected]})
        # The socket is outside the writable subtree, and never mounted into the child.
        args = command(root, source_views, arguments)
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                                close_fds=True, timeout=20)
        report = json.loads(result.stdout) if result.stdout.strip() else None
        evidence = {'command': args, 'exit': result.returncode, 'stdout': report,
                    'stderr': result.stderr, 'protected_canary_unchanged': before == digest(marker),
                    'host_namespace_ids': host_ns, 'manifest_sha256': digest(root / 'manifest.json')}
        (root / 'probe-result.json').write_text(json.dumps(evidence, indent=2) + '\n')
        if result.returncode != 0 or not evidence['protected_canary_unchanged']:
            failed = [k for k, passed in (report or {}).get('checks', {}).items() if not passed]
            raise IsolationRefused('Negative probe failed; no application launch allowed: '
                                   + ', '.join(failed) + result.stderr)
        return evidence
    finally:
        for s in sockets:
            s.close()
        fs_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True, help='Ignored private source context')
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    private = repository / '.local/r04'
    context_path = args.context.absolute()
    if private not in context_path.parents or context_path.is_symlink():
        raise IsolationRefused('Context must be a private R04 file')
    owned_ancestors(context_path.parent)
    if context_path.stat().st_uid != os.getuid() or context_path.stat().st_nlink != 1:
        raise IsolationRefused('Private context must be an owned, unshared file')
    context = json.loads(context_path.read_text())
    sources = sources_from_context(context)
    protected = [Path(p).resolve(strict=True) for p in context['protected_roots']]
    root = construct(private / 'profiles', protected, sources, uuid.uuid4().hex)
    result = run_probe(root, sources, protected)
    (root / 'probe-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'profile': str(root), 'exit': result['exit'],
                      'passed_checks': sum(result['stdout']['checks'].values()),
                      'required_checks': len(result['stdout']['checks']),
                      'runtime': 'NOT_RUN; prefix skeleton only'}, indent=2))


if __name__ == '__main__':
    main()
