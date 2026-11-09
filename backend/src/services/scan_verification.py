from typing import Protocol


VENDOR_SOURCES = {
    'Ubuntu Security Notices', 'Debian Security Advisories',
    'Red Hat Security Advisories', 'openEuler Security Advisories',
    'Kylin Security Advisories', 'UnionTech Security Advisories',
    'Anolis Security Advisories',
}


class AdvisoryEvidence(Protocol):
    cve_id: str
    advisory_id: str
    source_name: str


class FindingEvidence(AdvisoryEvidence, Protocol):
    package_name: str
    fixed_version: str


def record_remediation_check(
    checks: list[dict[str, object]] | None, advisory: AdvisoryEvidence,
    package: dict[str, object], installed: str | None, vulnerable: bool | None,
) -> None:
    if checks is None or advisory.source_name not in VENDOR_SOURCES:
        return
    if installed is not None and vulnerable is not False:
        return
    checks.append({
        'cve_id': advisory.cve_id, 'advisory_id': advisory.advisory_id,
        'source_name': advisory.source_name, 'package_name': package['name'],
        'fixed_version': package['fixed_version'], 'installed_version': installed,
        'outcome': 'not_installed' if installed is None else 'not_vulnerable',
    })


def verified_versions(
    previous: list[FindingEvidence], checks: list[dict[str, object]],
    versions: dict[str, str],
) -> str | None:
    if not previous:
        return None
    resolved: dict[str, str] = {}
    for finding in previous:
        matching = [check for check in checks if all((
            check.get('cve_id') == finding.cve_id,
            check.get('package_name') == finding.package_name,
            check.get('advisory_id') == finding.advisory_id,
            check.get('source_name') == finding.source_name,
            finding.source_name in VENDOR_SOURCES,
            check.get('fixed_version') == finding.fixed_version,
        ))]
        version = versions.get(finding.package_name)
        valid = any(
            (version is None and check.get('outcome') == 'not_installed'
             and check.get('installed_version') is None)
            or (bool(version) and check.get('outcome') == 'not_vulnerable'
                and check.get('installed_version') == version)
            for check in matching
        )
        if not valid:
            return None
        resolved[finding.package_name] = version if version is not None else 'not_installed'
    if len(resolved) == 1:
        return next(iter(resolved.values()))
    return '; '.join(f'{name}={version}' for name, version in sorted(resolved.items()))
