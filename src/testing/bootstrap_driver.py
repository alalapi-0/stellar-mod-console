"""Fixed R04-b sequence inside a closed namespace; never a general launcher."""
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from . import probe
else:
    import probe

SENTINEL = b'STELLAR_R04_B_SENTINEL\r\n'
SENTINEL_PATH = '/work/compatdata/pfx/drive_c/stellar-r04-bootstrap.txt'
COMMANDS = [
    ['/usr/bin/python3', '-B', '/proton/proton', 'getcompatpath', '/work'],
    ['/usr/bin/python3', '-B', '/proton/proton', 'runinprefix', 'C:\\windows\\system32\\cmd.exe',
     '/d', '/c', 'echo STELLAR_R04_B_SENTINEL>C:\\stellar-r04-bootstrap.txt&&type C:\\stellar-r04-bootstrap.txt'],
    ['/proton/files/bin/wineserver', '-w'],
]
TIMEOUTS = [40, 15, 15]


def namespace_check(expected):
    report = probe.inspect(expected)
    checks = report['checks']
    checks['game_absent'] = not Path('/game').exists()
    checks['fake_client_empty'] = not list(Path('/work/fake-steam').iterdir())
    for path in ['/usr/lib/x86_64-linux-gnu/nvidia/wine',
                 '/lib/x86_64-linux-gnu/nvidia/wine', '/proton/contrib']:
        checks[path + '_masked'] = 'ro' in report['mounts'].get(path, []) and not list(Path(path).iterdir())
    checks['private_lock_overlay'] = 'rw' in report['mounts'].get('/proton/dist.lock', [])
    return report


def main():
    request = json.loads(sys.argv[1])
    if set(request) != {'mode', 'expected'} or request['mode'] not in ['dry', 'execute']:
        raise ValueError('Only fixed dry or execute sequence allowed')
    expected = request['expected']
    before = namespace_check(expected)
    result = {'before': before, 'steps': [], 'runtime': 'NOT_RUN'}
    if not all(before['checks'].values()):
        print(json.dumps(result)); return 1
    if request['mode'] == 'dry':
        print(json.dumps(result)); return 0
    try:
        for args, timeout in zip(COMMANDS, TIMEOUTS):
            env = dict(os.environ)
            if args == COMMANDS[-1]:
                env['WINEPREFIX'] = '/work/compatdata/pfx'
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=env, close_fds=True)
            step = {'args': args, 'namespace_pid': child.pid}
            result['steps'].append(step)
            try:
                stdout, stderr = child.communicate(timeout=timeout)
                step.update(exit=child.returncode, stdout=stdout, stderr=stderr)
            except subprocess.TimeoutExpired:
                child.kill(); stdout, stderr = child.communicate()
                step.update(exit=child.returncode, stdout=stdout, stderr=stderr, timeout=True)
                raise RuntimeError('Fixed step timed out; namespace teardown required')
            if child.returncode != 0:
                raise RuntimeError('Fixed step failed')
        result['sentinel_bytes_match'] = Path(SENTINEL_PATH).read_bytes() == SENTINEL
        result['windows_stdout_match'] = result['steps'][1]['stdout'] == 'STELLAR_R04_B_SENTINEL\n'
        result['compatpath_match'] = result['steps'][0]['stdout'].strip() == 'Z:\\work'
        # These identities belong to this private PID namespace, not the host.
        result['remaining_namespace_pids'] = sorted(int(p.name) for p in Path('/proc').iterdir()
                                                  if p.name.isdecimal() and int(p.name) not in [1, os.getpid()])
        after_expected = dict(expected, prefix_empty=False)
        result['after'] = namespace_check(after_expected)
        result['runtime'] = 'PRIVATE_PREFIX_AND_FIXED_WINDOWS_CLI'
        result['passed'] = (result['sentinel_bytes_match'] and result['windows_stdout_match']
                            and result['compatpath_match'] and not result['remaining_namespace_pids']
                            and all(result['after']['checks'].values()))
    except Exception as error:
        result.update(passed=False, error=str(error))
    print(json.dumps(result)); return 0 if result.get('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
