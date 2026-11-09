#!/usr/bin/python3
"""Administrator-installed SSH forced-command gateway; no interactive shell."""
import json
import os
from pathlib import Path
import pwd
import selectors
import stat
import subprocess
import sys
import time

PROTOCOL = 'linuxops-readonly-v1'
ACTIONS = {'linux.uptime', 'linux.memory', 'linux.filesystem', 'linux.inodes',
           'linux.failed_units', 'linux.processes'}
MAX_BYTES = 16000


class GatewayDenied(RuntimeError):
    pass


def validate_identity() -> None:
    if os.getuid() == 0 or os.geteuid() != os.getuid() or os.environ.get('SSH_TTY'):
        raise GatewayDenied('restricted_non_root_identity_required')
    script = Path(__file__).resolve()
    for path in (script, *script.parents):
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise GatewayDenied('gateway_must_be_root_owned_and_not_writable')


def parse_action(command: str) -> str:
    if not command.startswith(PROTOCOL + ':'):
        raise GatewayDenied('unknown_protocol')
    action = command[len(PROTOCOL) + 1:]
    if action != 'handshake' and action not in ACTIONS:
        raise GatewayDenied('unknown_action')
    return action


def _read(path: str, limit: int = 8192) -> str:
    with open(path, encoding='utf-8') as source:
        return source.read(limit)


def _filesystem(inodes: bool) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in _read('/proc/self/mounts', 16000).splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] in {'proc', 'sysfs', 'devtmpfs', 'tmpfs', 'cgroup', 'cgroup2'}:
            continue
        mount = fields[1].replace('\\040', ' ')
        if mount in seen or len(rows) >= 32:
            continue
        seen.add(mount)
        try:
            usage = os.statvfs(mount)
            total, free = (usage.f_files, usage.f_favail) if inodes else (
                usage.f_blocks * usage.f_frsize, usage.f_bavail * usage.f_frsize)
            rows.append({'mount': mount[:200], 'total': total, 'available': free})
        except OSError:
            rows.append({'mount': mount[:200], 'error': 'unavailable'})
    return {'filesystems': rows, 'unit': 'inodes' if inodes else 'bytes'}


def _failed_units() -> dict[str, object]:
    binary = next((path for path in ('/usr/bin/systemctl', '/bin/systemctl') if Path(path).exists()), None)
    if binary is None:
        raise GatewayDenied('systemd_unavailable')
    info = Path(binary).resolve().stat()
    if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise GatewayDenied('untrusted_executable')
    command = [binary, '--failed', '--no-legend', '--no-pager', '--plain']
    output, errors = bytearray(), bytearray()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'}, shell=False) as child:
        with selectors.DefaultSelector() as streams:
            streams.register(child.stdout, selectors.EVENT_READ, output)
            streams.register(child.stderr, selectors.EVENT_READ, errors)
            deadline = time.monotonic() + 4
            try:
                while streams.get_map():
                    if time.monotonic() > deadline:
                        raise GatewayDenied('command_timeout')
                    for item, _ in streams.select(0.05):
                        chunk = os.read(item.fileobj.fileno(), 2048)
                        if chunk:
                            item.data.extend(chunk)
                            if len(output) + len(errors) > MAX_BYTES:
                                raise GatewayDenied('output_limit')
                        else:
                            streams.unregister(item.fileobj)
                if child.wait(timeout=1) != 0:
                    raise GatewayDenied('systemd_unavailable')
            except Exception:
                child.kill()
                child.wait(timeout=1)
                raise
    units = [line.split()[:4] for line in output.decode('utf-8', 'replace').splitlines()[:64]]
    return {'failed_units': units}


def _processes() -> dict[str, object]:
    rows: list[dict[str, int]] = []
    for path in sorted(Path('/proc').iterdir(), key=lambda item: item.name)[:1024]:
        if not path.name.isdigit():
            continue
        try:
            fields = _read(str(path / 'stat'), 4096).rsplit(')', 1)[1].split()
            rows.append({'pid': int(path.name), 'cpu_ticks': int(fields[11]) + int(fields[12]),
                         'rss_pages': int(fields[21])})
        except (OSError, ValueError, IndexError):
            continue
    return {'processes': sorted(rows, key=lambda item: item['cpu_ticks'], reverse=True)[:15]}


def observe(action: str) -> dict[str, object]:
    if action == 'linux.uptime':
        return {'uptime_seconds': float(_read('/proc/uptime', 128).split()[0]),
                'load': _read('/proc/loadavg', 128).split()[:3]}
    if action == 'linux.memory':
        memory = {}
        for line in _read('/proc/meminfo').splitlines():
            key, value = line.split(':', 1)
            if key in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
                memory[key] = int(value.strip().split()[0])
        return {'memory_kib': memory}
    if action in {'linux.filesystem', 'linux.inodes'}:
        return _filesystem(action == 'linux.inodes')
    if action == 'linux.failed_units':
        return _failed_units()
    if action == 'linux.processes':
        return _processes()
    raise GatewayDenied('unknown_action')


def run(command: str) -> dict[str, object]:
    validate_identity()
    action = parse_action(command)
    result: dict[str, object] = {'protocol': PROTOCOL, 'action': action, 'status': 'completed'}
    if action == 'handshake':
        result.update(uid=os.geteuid(), username=pwd.getpwuid(os.geteuid()).pw_name, writes_allowed=False)
    else:
        result['evidence'] = observe(action)
    encoded = json.dumps(result, ensure_ascii=True)
    if len(encoded.encode()) > MAX_BYTES:
        raise GatewayDenied('output_limit')
    return result


def main() -> int:
    try:
        result = run(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    except Exception:
        print(json.dumps({'protocol': PROTOCOL, 'status': 'failed', 'error': 'read_denied'}))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
