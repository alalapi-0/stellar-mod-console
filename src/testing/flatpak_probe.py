"""Self-authored Flatpak build --runtime boundary probe.

This module prepares and, only after an exact gate, runs one build-init plus
one build. It is not a Steam, game, login, or bwrap launcher.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from src.testing.isolation import disjoint, owned_ancestors

CONTRACT = 'stellar-mod-console/R04-n/v1'
FLATPAK = '/usr/bin/flatpak'
APP_ID = 'org.stellarmodconsole.R04nProbe'
RUNTIME_NAME = 'org.freedesktop.Platform'
BRANCH = '25.08'
PLATFORM_REF = 'org.freedesktop.Platform/x86_64/25.08'
NVIDIA_REF = 'org.freedesktop.Platform.GL.nvidia-595-91-07/x86_64/1.4'
PINNED_FLATPAK_SHA = '80a4dec2841fb97c6c6cfdf1fa20d09de8d43f6a72ede2fe5090522d2669302f'
PINNED_PLATFORM_SHA = 'd02891b938f979b23d23e532d1c7200d31d7d996a079ff3718a7703fc8b0fd27'
PINNED_NVIDIA_SHA = 'cc504b55865933fbd93aee1fd0e2dbbeadef75dc1076c19750b5c14a08733efa'
PINNED_NVIDIA_COMMIT = '0a0223df301477e0b309653c1629a13d09048c72f8b35b4b20fb73a70d60c355'
OUTPUT_LIMIT = 65536
ENV_ALLOWLIST = ('HOME', 'PATH')
ENV_OPTIONAL = ('LANG', 'LC_ALL', 'LOGNAME', 'USER')
SEMANTIC_FILES = ('docs/contracts/R04-n.json', 'src/testing/flatpak_probe.py',
                  'src/testing/README.md', 'tests/test_flatpak_probe.py')
EXPECTED_VERSION = '1.16.6'
REPOSITORY = Path(__file__).resolve().parents[2]
BASE = REPOSITORY / '.local/r04/route-prerequisites'
SENTINEL_NAME = 'r04n-host-only'
SOCKETS = ('x11', 'wayland', 'fallback-x11', 'pulseaudio', 'system-bus', 'session-bus',
           'ssh-auth', 'pcsc', 'cups', 'gpg-agent', 'inherit-wayland-socket')
DEVICES = ('dri', 'usb', 'input', 'kvm', 'shm', 'all')
FILESYSTEM_DENIALS = ('host', 'home', 'host-os', 'host-etc')
HOST_EXACT = (
    'bwrap: Creating new namespace failed: Operation not permitted',
    'bwrap: No permissions to create a new namespace, likely because the kernel does not allow '
    'non-privileged user namespaces. See <https://deb.li/bubblewrap> or '
    '<file:///usr/share/doc/bubblewrap/README.Debian.gz>.',
)
PREREQ_EXACT = (
    "error: No such ref 'runtime/org.freedesktop.Platform/x86_64/25.08'",
    "error: No such ref 'runtime/org.freedesktop.Sdk/x86_64/25.08'",
)
FORBIDDEN_ENV = ('HOME', 'FLATPAK_USER_DIR', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_RUNTIME_DIR')
BASE_CHECKS = ('app_readonly', 'var_readonly', 'usr_readonly', 'etc_readonly', 'home_readonly',
               'ephemeral_only', 'sentinel_absent', 'host_home_absent', 'protected_routes_absent',
               'dbus_absent', 'display_absent', 'audio_absent', 'input_absent', 'gpu_device_absent',
               'network_unshared', 'ipc_unshared', 'capabilities_dropped', 'process_separated',
               'runtime_context_exact', 'unregistered_writes_denied')
PAYLOAD_KEYS = BASE_CHECKS + ('nvidia_marker_present', 'nvidia_marker_ambiguous')
PROBE_SCRIPT = r"""#!/bin/sh
# R04N_SELF_AUTHORED_PROBE
set -u
yes() { if [ "$1" = yes ]; then printf true; else printf false; fi; }
denied() {
  target="$1/r04n-write-probe"
  if ( : > "$target" ) 2>/dev/null; then
    rm -f "$target"
    printf no
  else
    printf yes
  fi
}
tmp_ok=yes
for spec in "/tmp tmpfs" "/dev/shm tmpfs"; do
  set -- $spec
  if ( : > "$1/r04n-write-probe" ) 2>/dev/null; then
    rm -f "$1/r04n-write-probe"
    kind=$(awk -v path="$1" '$5==path { for (i=6; i<=NF; i++) if ($i=="-") { print $(i+1); exit } }' /proc/self/mountinfo 2>/dev/null || true)
    if [ "$kind" != tmpfs ]; then tmp_ok=no; fi
  fi
done
sentinel=no
if [ -f /app/sentinel.path ] && [ ! -L /app/sentinel.path ]; then
  sentinel_path=$(head -c 4096 /app/sentinel.path || true)
  case "$sentinel_path" in
    /*) ;;
    *) sentinel_path= ;;
  esac
  if [ -n "$sentinel_path" ] && [ ! -e "$sentinel_path" ] && [ ! -L "$sentinel_path" ]; then
    sentinel=yes
  fi
fi
for path in /r04n-host-only /app/r04n-host-only /var/r04n-host-only /home/r04n-host-only \
    /run/host/r04n-host-only /usr/r04n-host-only; do
  if [ -e "$path" ] || [ -L "$path" ]; then sentinel=no; fi
done
if [ -n "${HOME:-}" ] && { [ -e "$HOME/r04n-host-only" ] || [ -L "$HOME/r04n-host-only" ]; }; then
  sentinel=no
fi
info=/.flatpak-info
host_home=yes
routes=yes
if [ -e /run/host ] || [ -e /var/run/host ] || [ -e /mnt ]; then routes=no; host_home=no; fi
if [ -f "$info" ] && grep -E -q 'filesystems=([^;\n]*;)*(host|home)(;|$)' "$info"; then host_home=no; fi
dbus=yes
if [ -e /run/dbus ] || [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] || [ -n "${DBUS_SYSTEM_BUS_ADDRESS:-}" ]; then dbus=no; fi
display=yes
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ] || [ -e /tmp/.X11-unix ]; then display=no; fi
audio=yes
if [ -e /dev/snd ] || [ -n "${PULSE_SERVER:-}" ]; then audio=no; fi
input=yes
if [ -e /dev/input ]; then input=no; fi
gpu=yes
if [ -e /dev/dri ] || [ -e /dev/nvidiactl ] || [ -e /dev/nvidia0 ]; then gpu=no; fi
network=yes
if [ -r /proc/net/route ]; then
  lines=$(wc -l < /proc/net/route | tr -d ' ')
  if [ "$lines" != 1 ]; then network=no; fi
else
  network=no
fi
if [ -f "$info" ] && grep -E -q 'shared=([^;\n]*;)*network(;|$)' "$info"; then network=no; fi
ipc=yes
if [ -f "$info" ] && grep -E -q 'shared=([^;\n]*;)*ipc(;|$)' "$info"; then ipc=no; fi
caps=no
process_separated=no
if [ -r /proc/self/status ]; then
  eff=$(awk '/^CapEff:/ { print $2; exit }' /proc/self/status)
  if [ "$eff" = 0000000000000000 ]; then caps=yes; fi
  nspid=$(awk '/^NSpid:/ { print NF-1; exit }' /proc/self/status)
  if [ "${nspid:-0}" -ge 2 ]; then process_separated=yes; fi
fi
runtime_exact=no
if [ -f "$info" ] && grep -E -q '(^|/)org\.freedesktop\.Platform/x86_64/25\.08($|[^0-9])' "$info"; then
  runtime_exact=yes
fi
writes=yes
for dir in / /usr /etc /opt /srv /run /dev /sys /proc /app /var /home; do
  if [ "$(denied "$dir")" != yes ]; then writes=no; fi
done
nvidia=no
ambiguous=no
expected=$(head -c 64 /app/nvidia.sha256 2>/dev/null || true)
if ! printf '%s' "$expected" | grep -E -q '^[0-9a-f]{64}$'; then
  expected=
fi
for base in /usr/lib/x86_64-linux-gnu/GL /usr/lib/GL; do
  dir="$base/nvidia-595-91-07"
  if [ -d "$dir" ] && [ ! -L "$dir" ]; then
    meta="$dir/metadata"
    if [ -f "$meta" ] && [ ! -L "$meta" ] && [ -n "$expected" ]; then
      size=$(wc -c < "$meta" | tr -d ' ')
      digest=
      if [ "${size:-99999}" -le 8192 ]; then
        digest=$(sha256sum "$meta" | awk '{ print $1; exit }')
      fi
      if [ "$digest" = "$expected" ]; then
        nvidia=yes
      else
        ambiguous=yes
      fi
    else
      ambiguous=yes
    fi
    if [ -L "$base/default" ]; then
      target=$(readlink "$base/default")
      if [ "$target" != nvidia-595-91-07 ] && [ "$target" != ./nvidia-595-91-07 ]; then ambiguous=yes; fi
    else
      ambiguous=yes
    fi
  fi
done
printf '%s\n' "{\"app_readonly\":$(yes $(denied /app)),\"var_readonly\":$(yes $(denied /var)),\"usr_readonly\":$(yes $(denied /usr)),\"etc_readonly\":$(yes $(denied /etc)),\"home_readonly\":$(yes $(denied /home)),\"ephemeral_only\":$(yes $tmp_ok),\"sentinel_absent\":$(yes $sentinel),\"host_home_absent\":$(yes $host_home),\"protected_routes_absent\":$(yes $routes),\"dbus_absent\":$(yes $dbus),\"display_absent\":$(yes $display),\"audio_absent\":$(yes $audio),\"input_absent\":$(yes $input),\"gpu_device_absent\":$(yes $gpu),\"network_unshared\":$(yes $network),\"ipc_unshared\":$(yes $ipc),\"capabilities_dropped\":$(yes $caps),\"process_separated\":$(yes $process_separated),\"runtime_context_exact\":$(yes $runtime_exact),\"unregistered_writes_denied\":$(yes $writes),\"nvidia_marker_present\":$(yes $nvidia),\"nvidia_marker_ambiguous\":$(yes $ambiguous)}"
"""


class Refused(ValueError):
    pass


def payload_bytes():
    return PROBE_SCRIPT.encode()


def payload_digest():
    return hashlib.sha256(payload_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def stamp(path):
    st = Path(path).lstat()
    return [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def sha_file(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Refused('HASH_NOT_REGULAR')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        if stamp(path)[1:4] != [after.st_ino, after.st_mode, after.st_size]:
            raise Refused('HASH_FILE_CHANGED')
    return digest


def ancestors(path):
    try:
        owned_ancestors(Path(path))
    except Exception as error:
        raise Refused('ANCESTOR') from error


def launch_environment(overrides=None):
    if overrides:
        raise Refused('ENVIRONMENT_REDIRECTION')
    env = {}
    for name in ENV_ALLOWLIST:
        if name not in os.environ or not isinstance(os.environ[name], str) or os.environ[name] == '':
            raise Refused('ENVIRONMENT_ALLOWLIST')
        env[name] = os.environ[name]
    for name in ENV_OPTIONAL:
        value = os.environ.get(name)
        if value:
            env[name] = value
    home = env['HOME']
    if not home.startswith('/') or '..' in Path(home).parts:
        raise Refused('ENVIRONMENT_REDIRECTION')
    for name in ('FLATPAK_USER_DIR', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_RUNTIME_DIR', 'DISPLAY',
                 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        if name in env:
            raise Refused('ENVIRONMENT_REDIRECTION')
    return env


def semantic_candidate():
    digest = hashlib.sha256()
    for rel in SEMANTIC_FILES:
        data = (REPOSITORY / rel).read_bytes()
        digest.update(rel.encode() + b'\0' + data + b'\0')
    return digest.hexdigest()


def ancestor_snapshot(path):
    path = Path(path)
    if BASE != path and BASE not in path.parents:
        raise Refused('PROFILE_SCOPE')
    rows = []
    current = path
    while True:
        if current.is_symlink() or not current.is_dir():
            raise Refused('DIRECTORY_PERMISSION')
        st = current.lstat()
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise Refused('DIRECTORY_PERMISSION')
        rows.append((str(current), st.st_dev, st.st_ino, st.st_mode))
        if current == BASE:
            break
        current = current.parent
    return tuple(rows)


def assert_snapshot(snapshot):
    for path, dev, ino, mode in snapshot:
        st = Path(path).lstat()
        if (st.st_dev, st.st_ino, st.st_mode) != (dev, ino, mode) or Path(path).is_symlink():
            raise Refused('ANCESTOR_CHANGED')


def app_paths(home):
    home = Path(home)
    return (home / '.local/share/flatpak/app' / APP_ID, Path('/var/lib/flatpak/app') / APP_ID,
            home / '.var/app' / APP_ID)


def assert_app_paths_absent(home):
    for path in app_paths(home):
        if path.exists() or path.is_symlink():
            raise Refused('APP_PATH_RESIDUE')


def require_build_dir(path):
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts or path.name != 'build' or
            any(c in str(path) for c in ' \n\t\'";$`')):
        raise Refused('BUILD_DIRECTORY')
    return path


def build_init_command(directory):
    directory = str(require_build_dir(directory))
    return [FLATPAK, 'build-init', directory, APP_ID, RUNTIME_NAME, RUNTIME_NAME, BRANCH]


def build_command(directory):
    directory = str(require_build_dir(directory))
    command = [FLATPAK, 'build', '--runtime', '--readonly', '--die-with-parent',
               '--unshare=network', '--unshare=ipc', '--filesystem=host:reset']
    command += ['--nosocket=' + name for name in SOCKETS]
    command += ['--nodevice=' + name for name in DEVICES]
    command += ['--nofilesystem=' + name for name in FILESYSTEM_DENIALS]
    command += ['--build-dir=/app', directory, '/bin/sh', '/app/probe.sh']
    validate_build_command(command, directory)
    return command


def validate_build_command(command, directory):
    directory = str(require_build_dir(directory))
    if not isinstance(command, list) or any(not isinstance(arg, str) for arg in command):
        raise Refused('COMMAND_SHAPE')
    for arg in command:
        if (arg in ('--with-appdir', '--sandbox', '--env') or arg.startswith((
                '--bind-mount', '--env=', '--env-fd=', '--unset-env', '--share=', '--socket=',
                '--device=', '--allow=', '--metadata=', '--persist=',
                '--own-name=', '--talk-name=', '--system-', '--a11y-', '--usb=', '--log-'))):
            raise Refused('FORBIDDEN_OPTION')
        if arg.startswith('--filesystem=') and arg != '--filesystem=host:reset':
            raise Refused('FORBIDDEN_OPTION')
        if arg == 'bwrap' or arg.endswith('/bwrap'):
            raise Refused('DIRECT_OR_NESTED_BWRAP')
    if any(command[i:i + 2] == ['flatpak', 'run'] or arg == 'flatpak run' for i, arg in enumerate(command)):
        raise Refused('FORBIDDEN_OPTION')
    for flag in ('--runtime', '--readonly', '--die-with-parent', '--unshare=network', '--unshare=ipc'):
        if flag not in command:
            raise Refused('MISSING_DENIAL')
    if any('--nosocket=' + name not in command for name in SOCKETS):
        raise Refused('MISSING_DENIAL')
    if any('--nodevice=' + name not in command for name in DEVICES):
        raise Refused('MISSING_DENIAL')
    if '--filesystem=host:reset' not in command:
        raise Refused('MISSING_DENIAL')
    if any('--nofilesystem=' + name not in command for name in FILESYSTEM_DENIALS):
        raise Refused('MISSING_DENIAL')
    if command != build_command_unchecked(directory):
        raise Refused('COMMAND_SHAPE')


def build_command_unchecked(directory):
    command = [FLATPAK, 'build', '--runtime', '--readonly', '--die-with-parent',
               '--unshare=network', '--unshare=ipc', '--filesystem=host:reset']
    command += ['--nosocket=' + name for name in SOCKETS]
    command += ['--nodevice=' + name for name in DEVICES]
    command += ['--nofilesystem=' + name for name in FILESYSTEM_DENIALS]
    return command + ['--build-dir=/app', str(directory), '/bin/sh', '/app/probe.sh']


def require_identity(identity):
    required = {'flatpak_version', 'flatpak_sha256', 'platform_ref', 'platform_metadata_sha256',
                'nvidia_ref', 'nvidia_commit', 'nvidia_metadata_sha256', 'payload_sha256'}
    if not isinstance(identity, dict) or set(identity) != required:
        raise Refused('UNEXPECTED_METADATA')
    if identity['flatpak_version'] != EXPECTED_VERSION or identity['flatpak_sha256'] != PINNED_FLATPAK_SHA:
        raise Refused('FLATPAK_IDENTITY')
    if identity['platform_ref'] != PLATFORM_REF or identity['platform_metadata_sha256'] != PINNED_PLATFORM_SHA:
        raise Refused('PLATFORM_IDENTITY')
    if (identity['nvidia_ref'] != NVIDIA_REF or identity['nvidia_commit'] != PINNED_NVIDIA_COMMIT or
            identity['nvidia_metadata_sha256'] != PINNED_NVIDIA_SHA):
        raise Refused('NVIDIA_IDENTITY')
    if identity['payload_sha256'] != payload_digest():
        raise Refused('PAYLOAD_IDENTITY')
    return identity


def pinned_identity():
    return {'flatpak_version': EXPECTED_VERSION, 'flatpak_sha256': PINNED_FLATPAK_SHA,
            'platform_ref': PLATFORM_REF, 'platform_metadata_sha256': PINNED_PLATFORM_SHA,
            'nvidia_ref': NVIDIA_REF, 'nvidia_commit': PINNED_NVIDIA_COMMIT,
            'nvidia_metadata_sha256': PINNED_NVIDIA_SHA, 'payload_sha256': payload_digest()}


def construct(parent, identifier):
    parent = Path(parent)
    if parent != BASE / 'profiles' or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise Refused('FIXED_PROFILE_SCOPE')
    ancestors(parent)
    root = parent / identifier
    if root.exists() or root.is_symlink():
        raise Refused('PROFILE_REUSE')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(mode=0o700)
    (root / 'build').mkdir(mode=0o700)
    (root / SENTINEL_NAME).write_bytes(b'r04n-host-only\n')
    (root / 'probe.sh').write_bytes(payload_bytes())
    os.chmod(root / 'probe.sh', 0o500)
    manifest = {'contract': CONTRACT, 'root': str(root), 'app_id': APP_ID, 'copies': 0,
                'authentication': 'NOT_RUN', 'client_game_launch': False,
                'payload_sha256': payload_digest(), 'root_identity': stamp(root)[:3]}
    save(root / 'manifest.json', manifest)
    return root


def validate(root):
    root = Path(root)
    ancestors(root)
    if root.parent != BASE / 'profiles':
        raise Refused('PROFILE_SCOPE')
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    expected = {'contract', 'root', 'app_id', 'copies', 'authentication', 'client_game_launch',
                'payload_sha256', 'root_identity'}
    if set(manifest) != expected or manifest['contract'] != CONTRACT or manifest['root'] != str(root):
        raise Refused('UNEXPECTED_METADATA')
    if manifest['app_id'] != APP_ID or manifest['copies'] != 0 or manifest['client_game_launch'] is not False:
        raise Refused('UNEXPECTED_METADATA')
    if manifest['authentication'] != 'NOT_RUN' or manifest['payload_sha256'] != payload_digest():
        raise Refused('PAYLOAD_IDENTITY')
    if stamp(root)[:3] != manifest['root_identity']:
        raise Refused('PROFILE_CHANGED')
    allowed = {'build', SENTINEL_NAME, 'probe.sh', 'manifest.json'}
    entries = {p.relative_to(root).as_posix() for p in root.rglob('*')}
    if entries != allowed:
        raise Refused('PROFILE_NOT_FRESH')
    for name in entries:
        path = root / name
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or (stat.S_ISREG(st.st_mode) and st.st_nlink != 1):
            raise Refused('PROFILE_LINK_OR_OWNER')
        if name == 'build' and any(path.iterdir()):
            raise Refused('PROFILE_NOT_FRESH')
    if (root / 'probe.sh').read_bytes() != payload_bytes():
        raise Refused('PAYLOAD_IDENTITY')
    ancestor_snapshot(root)
    if not disjoint(root / 'build', root / SENTINEL_NAME):
        raise Refused('SENTINEL_OVERLAP')
    return manifest


def classify_init(exit_code, stderr):
    text = stderr.strip()
    if text in HOST_EXACT and exit_code != 0:
        return 'FLATPAK_HOST_DENIAL_CLASSIFIED'
    if text in PREREQ_EXACT and exit_code != 0:
        return 'FLATPAK_SUPPORTED_PREREQUISITE_MISSING'
    if exit_code != 0 and '\n' not in text and text.startswith('error: Unknown option'):
        return 'PROBE_ARGUMENT_DEFECT'
    return 'PROBE_FAILED_UNCLASSIFIED'


def classify_payload(payload):
    if not isinstance(payload, dict) or set(payload) != set(PAYLOAD_KEYS):
        return 'PROBE_FAILED_UNCLASSIFIED'
    if any(payload[key] is not True for key in BASE_CHECKS):
        return 'PROBE_FAILED_UNCLASSIFIED'
    if payload['nvidia_marker_present'] is True and payload['nvidia_marker_ambiguous'] is False:
        return 'FLATPAK_RUNTIME_BOUNDARY_PROVED_WITH_DEFAULT_NVIDIA_EXTENSION'
    return 'FLATPAK_BASE_BOUNDARY_PROVED_NVIDIA_EXTENSION_NOT_PROVED'


def classify_build(exit_code, stdout, stderr):
    stdout = '' if stdout is None else stdout
    stderr = '' if stderr is None else stderr
    if len(stdout.encode()) > OUTPUT_LIMIT or len(stderr.encode()) > OUTPUT_LIMIT:
        return 'PROBE_FAILED_UNCLASSIFIED'
    text = stderr.strip()
    if text in HOST_EXACT:
        if exit_code == 0 or stdout.strip():
            return 'PROBE_FAILED_UNCLASSIFIED'
        return 'FLATPAK_HOST_DENIAL_CLASSIFIED'
    if exit_code != 0 and '\n' not in text and text.startswith('error: Unknown option'):
        return 'PROBE_ARGUMENT_DEFECT'
    try:
        payload = json.loads(stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        return 'PROBE_FAILED_UNCLASSIFIED'
    if exit_code != 0:
        return 'PROBE_FAILED_UNCLASSIFIED'
    return classify_payload(payload)


def classify_timeout():
    return 'PROBE_TIMEOUT'


def apply_residue(outcome, residue):
    if residue and outcome.startswith('FLATPAK_'):
        return 'PROBE_FAILED_UNCLASSIFIED'
    return outcome


def require_new_result(base):
    ancestors(base)
    output = Path(base) / 'result.json'
    if os.path.lexists(output):
        raise Refused('RESULT_REUSE_OR_LINK')
    return output


def write_result(base, result):
    output = require_new_result(base)
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        failure = Refused('RESULT_PUBLICATION_COLLISION')
        failure.evidence = result
        raise failure from error
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')


def require_registration():
    path = BASE / 'implementation.json'
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise Refused('REGISTRATION_REQUIRED')
    registration = json.loads(path.read_text())
    if (registration.get('contract_id') != CONTRACT or
            registration.get('semantic_candidate') != semantic_candidate()):
        raise Refused('REGISTRATION_MISMATCH')
    return registration


def gate_matches(base, registration_sha):
    path = Path(base) / 'gate.json'
    if not path.is_file() or path.is_symlink():
        raise Refused('EXACT_FRESH_REVIEW_REQUIRED')
    gate = json.loads(path.read_text())
    expected = {'contract': CONTRACT, 'registration_sha256': registration_sha,
                'judge': 'PASS', 'governor': 'APPROVE_FLATPAK_PROBE'}
    if gate != expected:
        raise Refused('EXACT_FRESH_REVIEW_REQUIRED')


def install_payload(files, root):
    target = files / 'probe.sh'
    sentinel = files / 'sentinel.path'
    nvidia = files / 'nvidia.sha256'
    for path in (target, sentinel, nvidia):
        if os.path.lexists(path):
            raise Refused('PAYLOAD_REUSE')
    target.write_bytes(payload_bytes())
    os.chmod(target, 0o500)
    sentinel.write_text(str(root / SENTINEL_NAME) + '\n')
    os.chmod(sentinel, 0o400)
    nvidia.write_text(PINNED_NVIDIA_SHA + '\n')
    os.chmod(nvidia, 0o400)
    if sha_file(target) != payload_digest() or nvidia.read_text().strip() != PINNED_NVIDIA_SHA:
        raise Refused('PAYLOAD_IDENTITY')


def execute(root, identity, registration_sha, runner, home):
    root = Path(root)
    manifest = validate(root)
    snapshot = ancestor_snapshot(root)
    require_new_result(BASE)
    require_registration()
    gate_matches(BASE, registration_sha)
    require_identity(identity)
    build_dir = root / 'build'
    init_command = build_init_command(build_dir)
    command = build_command(build_dir)
    if command != build_command_unchecked(build_dir):
        raise Refused('COMMAND_SHAPE')
    if manifest['payload_sha256'] != payload_digest():
        raise Refused('PAYLOAD_IDENTITY')
    assert_app_paths_absent(home)
    env = launch_environment(None)
    assert_snapshot(snapshot)
    try:
        init = runner(init_command, 60, env)
    except subprocess.TimeoutExpired:
        write_result(BASE, {'outcome': classify_timeout(), 'stage': 'build-init', 'build_started': False})
        return classify_timeout()
    if init.returncode != 0:
        outcome = classify_init(init.returncode, bounded_stderr(init.stderr))
        write_result(BASE, {'outcome': outcome, 'stage': 'build-init', 'build_started': False})
        return outcome
    files = build_dir / 'files'
    if not files.is_dir() or files.is_symlink():
        write_result(BASE, {'outcome': 'FLATPAK_SUPPORTED_PREREQUISITE_MISSING', 'stage': 'build-init',
                            'build_started': False, 'distinction': 'FILES_DIRECTORY_ABSENT'})
        return 'FLATPAK_SUPPORTED_PREREQUISITE_MISSING'
    install_payload(files, root)
    assert_snapshot(snapshot)
    try:
        build = runner(command, 30, env)
    except subprocess.TimeoutExpired:
        write_result(BASE, {'outcome': classify_timeout(), 'stage': 'build', 'build_started': True})
        return classify_timeout()
    stdout, stderr = bound_pair(build.stdout, build.stderr)
    if stdout is None:
        write_result(BASE, {'outcome': 'PROBE_FAILED_UNCLASSIFIED', 'stage': 'build',
                            'build_started': True, 'distinction': 'OUTPUT_LIMIT'})
        return 'PROBE_FAILED_UNCLASSIFIED'
    outcome = classify_build(build.returncode, stdout, stderr)
    residue = any(path.exists() or path.is_symlink() for path in app_paths(home))
    outcome = apply_residue(outcome, residue)
    write_result(BASE, {'outcome': outcome, 'stage': 'build', 'build_started': True,
                        'platform_25_08_is_not_steam_26_08': True})
    return outcome


def bounded_stderr(text):
    stdout, stderr = bound_pair('', text)
    return '' if stderr is None else stderr


def bound_pair(stdout, stderr):
    stdout = '' if stdout is None else stdout
    stderr = '' if stderr is None else stderr
    if len(stdout.encode()) > OUTPUT_LIMIT or len(stderr.encode()) > OUTPUT_LIMIT:
        return None, None
    return stdout, stderr


def main():
    raise SystemExit('Flatpak execution requires a fresh Judge PASS and Governor approval')
