import re
import shlex
import socket

import paramiko
from paramiko.ssh_exception import NoValidConnectionsError

from src.models.asset import Asset
from src.models.credential import Credential
from src.models.knowledge import KnowledgeAdvisory
from src.models.scan_task import ScanType
from src.services.scan_verification import VENDOR_SOURCES, record_remediation_check
from src.services.service_identity import MAX_BANNER_PROBES, listen_port, probe_banner
from src.services.service_advisory_matching import SOURCE_NAME as UPSTREAM_SERVICE_SOURCE
from src.services.service_advisory_matching import match_service_advisories
from src.services.ssh_connection import open_client, parse_private_key

MAX_OUTPUT_BYTES = 5 * 1024 * 1024


class ScanCollectionError(RuntimeError):
    pass


def assess_asset(
    asset: Asset,
    credential: Credential,
    secret: str,
    advisories: list[KnowledgeAdvisory],
    scan_type: ScanType,
    expected_host_key: paramiko.PKey | None = None,
) -> dict[str, object]:
    client = _connect(asset, credential, secret, expected_host_key)
    try:
        os_data = _parse_os_release(_execute(client, 'cat /etc/os-release'))
        manager = _detect_package_manager(client)
        packages = _collect_packages(client, manager)
        services = (
            _collect_services(client, manager, packages)
            if scan_type != ScanType.HOST_VULNERABILITY else []
        )
        remediation_checks: list[dict[str, object]] = []
        findings = _match_advisories(
            client, manager, packages, advisories,
            os_data.get('id', asset.os_family.value), services, scan_type,
            _os_match_version(os_data), remediation_checks,
        )
        return {
            'asset_id': asset.id, 'asset_name': asset.name, 'status': 'scanned',
            'os_family': os_data.get('id', 'other'),
            'os_version': os_data.get('version_id', 'unknown'),
            'package_manager': manager, 'package_count': len(packages),
            'package_versions': packages,
            'remediation_checks': remediation_checks,
            'service_count': len(services), 'services': services, 'findings': findings,
        }
    finally:
        client.close()


def _connect(
    asset: Asset, credential: Credential, secret: str,
    expected_host_key: paramiko.PKey | None = None,
) -> paramiko.SSHClient:
    del expected_host_key
    client: paramiko.SSHClient | None = None
    try:
        client = open_client(asset, credential, secret)
    except (OSError, paramiko.SSHException, ValueError) as exc:
        if client is not None:
            client.close()
        detail = _connection_error_message(exc)
        raise ScanCollectionError(detail) from exc
    return client


def _connection_error_message(exc: Exception) -> str:
    if isinstance(exc, paramiko.AuthenticationException):
        return "SSH 登录认证失败：请检查用户名、密码或私钥"
    if isinstance(exc, NoValidConnectionsError):
        return "SSH 端口无法连接：请检查服务器地址、防火墙和端口"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "SSH 连接超时：请检查服务器是否在线及端口是否可达"
    if isinstance(exc, ValueError):
        return "SSH 私钥格式无效，请重新保存认证信息"
    if isinstance(exc, OSError):
        return f"SSH 网络连接失败：{type(exc).__name__}"
    return f"SSH 连接失败：{type(exc).__name__}"


def _same_host_key(left: paramiko.PKey, right: paramiko.PKey) -> bool:
    return left.get_name() == right.get_name() and left.asbytes() == right.asbytes()


def _execute(client: paramiko.SSHClient, command: str) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=30)
    output = stdout.read(MAX_OUTPUT_BYTES + 1)
    error = stderr.read(4096)
    exit_code = stdout.channel.recv_exit_status()
    if len(output) > MAX_OUTPUT_BYTES:
        raise ScanCollectionError('目标机采集结果超过 5 MiB 限制')
    if exit_code != 0:
        detail = error.decode('utf-8', errors='replace').strip()[:200]
        raise ScanCollectionError('只读采集命令失败' + ('：' + detail if detail else ''))
    return output.decode('utf-8', errors='replace')


def _detect_package_manager(client: paramiko.SSHClient) -> str:
    command = "sh -c 'if command -v dpkg-query >/dev/null; then echo dpkg; elif command -v rpm >/dev/null; then echo rpm; else echo unknown; fi'"
    manager = _execute(client, command).strip()
    if manager not in {'dpkg', 'rpm'}:
        raise ScanCollectionError('目标系统缺少受支持的软件包管理器')
    return manager


def _collect_packages(client: paramiko.SSHClient, manager: str) -> dict[str, str]:
    command = (
        "dpkg-query -W -f='${binary:Package}\t${Version}\t${source:Package}\n'"
        if manager == 'dpkg'
        else "rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\n'"
    )
    packages: dict[str, str] = {}
    for line in _execute(client, command).splitlines():
        fields = line.split('	')
        name, separator, version = fields[0], len(fields) > 1, fields[1] if len(fields) > 1 else ''
        if separator:
            packages[name.split(':', 1)[0]] = version
            if manager == 'dpkg' and len(fields) > 2:
                source = fields[2].split(':', 1)[0].split('(', 1)[0].strip()
                if source:
                    packages[f'__source__:{source}'] = version
    if not packages:
        raise ScanCollectionError('未采集到已安装软件包')
    return packages


def _collect_services(
    client: paramiko.SSHClient, manager: str, packages: dict[str, str]
) -> list[dict[str, str]]:
    command = "sh -c 'command -v ss >/dev/null && ss -H -lntup 2>/dev/null || true'"
    services: list[dict[str, str]] = []
    banner_attempts = 0
    for line in _execute(client, command).splitlines()[:500]:
        fields = line.split()
        if len(fields) >= 5:
            process = ' '.join(fields[5:])[:300]
            process_name, pid = _parse_process(process)
            executable, package_name = _resolve_service_package(client, manager, pid)
            can_probe = (
                fields[0].lower() in {'tcp', 'tcp6'}
                and listen_port(fields[4]) is not None
                and banner_attempts < MAX_BANNER_PROBES
            )
            banner = probe_banner(client, fields[0], fields[4]) if can_probe else ''
            if can_probe:
                banner_attempts += 1
            services.append({
                'protocol': fields[0], 'listen': fields[4],
                'process': process, 'process_name': process_name, 'pid': pid,
                'executable': executable, 'package_name': package_name,
                'installed_version': packages.get(package_name, ''), 'banner': banner,
            })
    return services


def _parse_process(value: str) -> tuple[str, str]:
    match = re.search(r'\(\("([^"]+)",pid=(\d+)', value)
    return (match.group(1), match.group(2)) if match else ('', '')


def _resolve_service_package(
    client: paramiko.SSHClient, manager: str, pid: str
) -> tuple[str, str]:
    if not pid.isdigit():
        return '', ''
    executable = _execute_optional(client, f'readlink -f /proc/{pid}/exe').strip()
    if not executable:
        return '', ''
    quoted = shlex.quote(executable)
    command = (
        f"rpm -qf --qf '%{{NAME}}\n' {quoted}"
        if manager == 'rpm'
        else f'dpkg-query -S {quoted}'
    )
    owner = _execute_optional(client, command).strip().splitlines()
    if not owner:
        return executable, ''
    package_name = owner[0].split(':', 1)[0] if manager == 'dpkg' else owner[0]
    return executable, package_name.strip()


def _execute_optional(client: paramiko.SSHClient, command: str) -> str:
    _, stdout, _ = client.exec_command(command, timeout=10)
    output = stdout.read(4097)
    if len(output) > 4096 or stdout.channel.recv_exit_status() != 0:
        return ''
    return output.decode('utf-8', errors='replace')


def _match_advisories(
    client: paramiko.SSHClient,
    manager: str,
    packages: dict[str, str],
    advisories: list[KnowledgeAdvisory],
    os_family: str,
    services: list[dict[str, str]] | None = None,
    scan_type: ScanType = ScanType.HOST_VULNERABILITY,
    os_version: str = '',
    remediation_checks: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    checks = remediation_checks if remediation_checks is not None else []
    aliases = {'rhel': {'rhel', 'centos', 'rocky', 'almalinux'}}
    # Keep vendor evidence ahead of supplemental sources so repeated CVEs are
    # deterministic in task results and downstream aggregation.
    ordered_advisories = sorted(
        advisories,
        key=lambda item: (
            0 if item.source_name in VENDOR_SOURCES else 1,
            item.cve_id,
            item.advisory_id,
        ),
    )
    for advisory in ordered_advisories:
        if advisory.source_name == UPSTREAM_SERVICE_SOURCE:
            continue
        accepted = aliases.get(advisory.os_family, {advisory.os_family})
        if os_family.lower() not in accepted:
            continue
        for package in sorted(
            advisory.affected_packages, key=lambda item: str(item.get('name', ''))
        ):
            supported_versions = package.get('os_versions', [])
            if supported_versions and not _matches_os_version(os_version, supported_versions):
                continue
            scope = package.get('package_scope', 'binary')
            package_name = str(package['name'])
            lookup_name = f'__source__:{package_name}' if scope == 'source' else package_name
            installed = packages.get(lookup_name)
            if installed is None:
                record_remediation_check(checks, advisory, package, None, None)
                continue
            matching_services = [
                item for item in (services or []) if item['package_name'] == package_name
            ]
            if scan_type == ScanType.SERVICE_VULNERABILITY and not matching_services:
                continue
            if scan_type == ScanType.SERVICE_VULNERABILITY:
                matching_services = _filter_banner_services(matching_services, package)
                if not matching_services:
                    continue
            vulnerable = _is_older(client, manager, package['name'], installed, package['fixed_version'])
            record_remediation_check(checks, advisory, package, installed, vulnerable)
            if vulnerable is False:
                continue
            confirmed = (
                vulnerable is True
                and advisory.source_name in VENDOR_SOURCES
                and not package.get('requires_manual_confirmation', False)
            )
            if scan_type == ScanType.SERVICE_VULNERABILITY:
                service = _summarize_service_evidence(matching_services)
                findings.append(_finding(advisory, package, installed, confirmed, service))
            else:
                service = matching_services[0] if matching_services else None
                findings.append(_finding(advisory, package, installed, confirmed, service))
    if scan_type != ScanType.HOST_VULNERABILITY:
        findings.extend(match_service_advisories(services or [], advisories, findings, checks))
    return findings


def _matches_os_version(installed: str, supported: list[str]) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r'[^a-z0-9.]', '', value.lower().removeprefix('openeuler'))

    current = normalize(installed)
    if not current:
        return False
    for item in supported:
        expected = normalize(item)
        if expected == current:
            return True
        if re.fullmatch(r'\d+', expected) and current.startswith(expected + '.'):
            return True
    return False


def _os_match_version(os_data: dict[str, str]) -> str:
    family = os_data.get('id', '').casefold()
    if family == 'openeuler':
        return os_data.get('version', os_data.get('version_id', ''))
    return os_data.get('version_id', os_data.get('version', ''))


def _filter_banner_services(
    services: list[dict[str, str]], package: dict[str, object]
) -> list[dict[str, str]]:
    keywords = package.get('service_banner_keywords', [])
    if not isinstance(keywords, list) or not keywords:
        return services
    normalized = [str(item).strip().casefold() for item in keywords if str(item).strip()]
    if not normalized:
        return services
    return [
        service for service in services
        if any(keyword in service.get('banner', '').casefold() for keyword in normalized)
    ]


def _summarize_service_evidence(services: list[dict[str, str]]) -> dict[str, str]:
    first = services[0]
    return {
        **first,
        'listen': '、'.join(sorted({item['listen'] for item in services})),
        'process_name': '、'.join(sorted({
            item['process_name'] or item['process'] for item in services
        })),
        'banner': '、'.join(sorted({
            item['banner'] for item in services if item.get('banner')
        })),
    }


def _is_older(
    client: paramiko.SSHClient, manager: str, package: str, installed: str, fixed: str
) -> bool | None:
    if manager == 'dpkg':
        command = 'dpkg --compare-versions ' + shlex.quote(installed) + ' lt ' + shlex.quote(fixed)
    elif manager == 'rpm':
        command = 'rpm -q --whatprovides ' + shlex.quote(package + ' >= ' + fixed) + ' >/dev/null 2>&1'
    else:
        return None
    _, stdout, _ = client.exec_command(command, timeout=10)
    exit_code = stdout.channel.recv_exit_status()
    if exit_code not in {0, 1}:
        return None
    return exit_code == 0 if manager == 'dpkg' else exit_code != 0


def _finding(
    advisory: KnowledgeAdvisory, package: dict[str, str], installed: str, confirmed: bool,
    service: dict[str, str] | None = None,
) -> dict[str, object]:
    conclusion = 'confirmed' if confirmed else 'manual_confirmation'
    evidence = [
        {'type': 'source', 'value': advisory.source_name},
        {'type': 'advisory', 'value': advisory.advisory_id},
        {'type': 'installed_version', 'value': installed},
        {'type': 'fixed_version', 'value': package['fixed_version']},
    ]
    if package.get('requires_manual_confirmation'):
        evidence.append({
            'type': 'limitation',
            'value': package.get('evidence_reason') or '公告与目标软件包对应关系需人工确认',
        })
    if service is not None:
        evidence.extend([
            {'type': 'service_listener', 'value': service['listen']},
            {'type': 'service_process', 'value': service['process_name'] or service['process']},
            {'type': 'service_executable', 'value': service['executable']},
            {'type': 'service_package', 'value': service['package_name']},
        ])
        if service.get('banner'):
            evidence.append({'type': 'service_banner', 'value': service['banner']})
    evidence.append({'type': 'conclusion', 'value': conclusion})
    return {
        'cve_id': advisory.cve_id, 'advisory_id': advisory.advisory_id,
        'title': advisory.title, 'severity': advisory.severity,
        'package_name': package['name'], 'installed_version': installed,
        'fixed_version': package['fixed_version'], 'source_name': advisory.source_name,
        'vendor_url': advisory.vendor_url, 'conclusion': conclusion,
        'confidence': 'high' if confirmed else 'low',
        'detection_scope': 'service' if service is not None else 'host',
        'service': service, 'evidence_chain': evidence,
    }


def _parse_os_release(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition('=')
        if separator:
            result[key.lower()] = value.strip().strip('"').strip("'")
    return result


def _parse_private_key(secret: str) -> paramiko.PKey:
    return parse_private_key(secret)
