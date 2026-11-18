from collections.abc import Iterable

from fastapi import Request

FEATURES: dict[str, tuple[str, bool]] = {
    'dashboard': ('综合总览', False),
    'server-monitoring': ('服务器监控', True),
    'assets': ('服务器资产', True),
    'cmdb': ('业务服务', True),
    'automation': ('自动化任务', True),
    'terminal': ('SSH 远程', True),
    'ops-assistant': ('AI 运维助手', True),
    'patches': ('补丁管理', True),
    'observability': ('服务监控', False),
    'probes': ('API 拨测', True),
    'alerts': ('活动告警', True),
    'incidents': ('故障管理', True),
    'scans': ('漏洞扫描', True),
    'schedules': ('定时扫描', True),
    'baselines': ('基线合规', True),
    'exposures': ('暴露面检测', True),
    'findings': ('漏洞治理', True),
    'knowledge': ('漏洞知识库', False),
    'agent': ('漏洞 AI 助手', True),
    'reports': ('安全报告', True),
    'system': ('平台状态', False),
    'audit-log': ('审计日志', False),
}

READ_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
ROUTE_FEATURE = {
    'dashboard': 'dashboard', 'monitoring': 'server-monitoring', 'assets': 'assets',
    'ssh-host-keys': 'assets', 'cmdb': 'cmdb', 'operations': 'automation',
    'terminal': 'terminal', 'ops-assistant': 'ops-assistant', 'patches': 'patches',
    'observability': 'observability', 'probes': 'probes', 'alerts': 'alerts',
    'alert-connectors': 'alerts', 'alert-notifications': 'alerts', 'diagnostic-jobs': 'alerts',
    'diagnostic-grants': 'alerts', 'recovery-verification': 'alerts', 'events': 'alerts',
    'incidents': 'incidents', 'scan-tasks': 'scans', 'scan-schedules': 'schedules',
    'baselines': 'baselines', 'exposures': 'exposures',
    'vulnerabilities': 'findings', 'knowledge': 'knowledge', 'ai-agent': 'agent',
    'reports': 'reports', 'system': 'system', 'audit-log': 'audit-log',
}

ASSET_PICKERS = frozenset({
    'assets', 'alerts', 'cmdb', 'terminal', 'ops-assistant', 'scans', 'schedules',
    'baselines', 'exposures', 'findings', 'incidents', 'probes',
})


def permits(permissions: frozenset[str], candidates: Iterable[str]) -> bool:
    return not permissions.isdisjoint(candidates)


def allowed_request(permissions: frozenset[str], request: Request) -> bool:
    route = request.scope.get('route')
    path = getattr(route, 'path', '')
    suffix = 'read' if request.method in READ_METHODS else 'write'
    if path == '/api/v1/assets' and suffix == 'read':
        return permits(permissions, (f'{page}:read' for page in ASSET_PICKERS))
    if path.startswith('/api/v1/terminal/assistant/'):
        return {'terminal:read', f'ops-assistant:{suffix}'} <= permissions
    if path == '/api/v1/alerts/{alert_id}/incident':
        return {'alerts:write', 'incidents:write'} <= permissions
    if path == '/api/v1/monitoring/overview' and suffix == 'read':
        return permits(permissions, ('server-monitoring:read', 'alerts:read'))
    if path == '/api/v1/monitoring/assets/{asset_id}/policy' and suffix == 'write':
        return permits(permissions, ('server-monitoring:write', 'alerts:write'))
    if path in {'/api/v1/system/settings', '/api/v1/system/settings/{category}'}:
        return False
    if path == '/api/v1/operations/integrations' and suffix == 'read':
        return {'automation:read', 'alerts:read'} <= permissions
    if path in {'/api/v1/assets/cleanup-audits', '/api/v1/alert-connectors'} or path.startswith('/api/v1/events'):
        return False
    return any(f'{ROUTE_FEATURE[tag]}:{suffix}' in permissions
               for tag in getattr(route, 'tags', ()) if tag in ROUTE_FEATURE)
