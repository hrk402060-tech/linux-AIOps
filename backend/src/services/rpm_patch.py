import re
import shlex
import threading
import time

import paramiko

from src.models.vulnerability import VulnerabilityFinding

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]{0,159}")
_EVR = re.compile(r"(?:(?P<epoch>0|[1-9][0-9]{0,9}):)?(?P<vr>[A-Za-z0-9][A-Za-z0-9.+~^_]*-[A-Za-z0-9][A-Za-z0-9.+~^_]*)")
_ARCH = re.compile(r"[A-Za-z0-9_]{1,32}")
_RPM_FORMAT = r"%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n"
_MANAGER = 'if command -v dnf >/dev/null; then echo dnf; elif command -v yum >/dev/null; then echo yum; fi'


class PatchUpdateError(ValueError):
    def __init__(self, message: str, *, verify_required: bool = False) -> None:
        super().__init__(message)
        self.verify_required = verify_required


def valid_package_name(value: str) -> bool:
    return _NAME.fullmatch(value) is not None


def is_rpm_advice(finding: VulnerabilityFinding) -> bool:
    return bool(
        finding.conclusion == "confirmed" and finding.confidence == "high"
        and finding.knowledge_version and valid_package_name(finding.package_name)
        and _EVR.fullmatch(finding.installed_version) and _EVR.fullmatch(finding.fixed_version)
        and _normalize(finding.installed_version) != _normalize(finding.fixed_version)
    )


def update_package(client: paramiko.SSHClient, name: str, current: str, target: str) -> None:
    if not (valid_package_name(name) and _EVR.fullmatch(current) and _EVR.fullmatch(target)):
        raise PatchUpdateError("缺少可安全核对的 RPM 软件包或版本")
    deadline = time.monotonic() + 85
    current, target = _normalize(current), _normalize(target)
    installed = _installed(client, name, deadline)
    if len(installed) != 1 or installed[0][0] != current:
        raise PatchUpdateError("当前安装版本与扫描记录不一致，或有多个架构/版本，请先复扫或人工处理")
    _, arch = installed[0]
    manager = _run(client, _MANAGER, deadline)[1].strip()
    if manager not in {"dnf", "yum"}:
        raise PatchUpdateError("目标服务器没有可用的 YUM/DNF")
    _require_exact_candidate(client, manager, name, target, arch, deadline)
    if not _is_upgrade(client, current, target, deadline):
        raise PatchUpdateError("RPM 版本核对未通过，禁止降级或重复安装")
    command = _command(manager, f"{name}-{target}.{arch}")
    try:
        code, _ = _run(client, command, deadline)
        if code != 0:
            raise PatchUpdateError("YUM/DNF 未完成更新，请核对仓库、目标版本、签名和执行权限后复扫", verify_required=True)
        if (target, arch) not in _installed(client, name, deadline):
            raise PatchUpdateError("未确认安装目标版本，请复扫并检查服务器的软件包事务", verify_required=True)
    except (OSError, paramiko.SSHException) as error:
        raise PatchUpdateError("连接中断或更新超时，请先复扫并检查服务器的软件包事务", verify_required=True) from error
    except PatchUpdateError as error:
        if error.verify_required:
            raise
        raise PatchUpdateError(str(error), verify_required=True) from error


def _normalize(value: str) -> str:
    return value if ":" in value else f"0:{value}"


def _installed(client: paramiko.SSHClient, name: str, deadline: float) -> list[tuple[str, str]]:
    code, output = _run(client, shlex.join(["rpm", "-q", "--qf", _RPM_FORMAT, "--", name]), deadline)
    result: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or fields[0] != name or not _EVR.fullmatch(fields[1]) or not _ARCH.fullmatch(fields[2]):
            raise PatchUpdateError("目标服务器 RPM 信息不完整，请人工核对")
        result.append((_normalize(fields[1]), fields[2]))
    if code != 0 or not result:
        raise PatchUpdateError("目标服务器未找到已安装的对应 RPM 软件包")
    return result


def _is_upgrade(client: paramiko.SSHClient, current: str, target: str, deadline: float) -> bool:
    current_epoch, current_vr = current.split(":", 1)
    target_epoch, target_vr = target.split(":", 1)
    if current_epoch != target_epoch:
        return int(current_epoch) < int(target_epoch)
    current_v, current_r = current_vr.split("-", 1)
    target_v, target_r = target_vr.split("-", 1)
    expr = (
        f'%{{lua:local v=rpm.vercmp("{current_v}","{target_v}");'
        f'print(v == 0 and rpm.vercmp("{current_r}","{target_r}") or v)}}'
    )
    code, output = _run(client, shlex.join(["rpm", "--eval", expr]), deadline)
    return code == 0 and output.strip() == "-1"


def _require_exact_candidate(
    client: paramiko.SSHClient, manager: str, name: str, target: str,
    arch: str, deadline: float,
) -> None:
    spec = f"{name}-{target}.{arch}"
    command = shlex.join([
        manager, "-q", "repoquery", "--available",
        "--qf", _RPM_FORMAT, "--", spec,
    ])
    code, output = _run(client, command, deadline)
    candidates = {
        (_normalize(parts[1]), parts[2])
        for line in output.splitlines()
        if len(parts := line.split("\t")) == 3 and parts[0] == name
        and _EVR.fullmatch(parts[1]) and _ARCH.fullmatch(parts[2])
    }
    if code != 0 or (target, arch) not in candidates:
        raise PatchUpdateError("已启用仓库中没有完全匹配的签名目标版本，未开始更新")


def _command(manager: str, nevra: str) -> str:
    # Keep repository/signature checks on, and constrain the requested update to one NEVRA.
    args = [
        "flock", "-n", "/run/lock/linux-aiops-patch.lock", manager, "-y",
        "--setopt=gpgcheck=1", "--setopt=*.gpgcheck=1",
        "--setopt=obsoletes=0",
    ]
    if manager == "dnf":
        args.extend(["--setopt=install_weak_deps=0", "upgrade"])
    else:
        args.append("update-to")
    invocation = shlex.join([*args, "--", nevra])
    return f'if [ "$(id -u)" = 0 ]; then exec {invocation}; else exec sudo -n -- {invocation}; fi'


def _run(client: paramiko.SSHClient, command: str, deadline: float) -> tuple[int, str]:
    remaining = deadline - time.monotonic()
    transport = client.get_transport()
    if remaining <= 0 or transport is None:
        raise TimeoutError()
    channel = transport.open_session(timeout=min(remaining, 8))
    chunks = bytearray()
    try:
        channel.set_combine_stderr(True)
        channel.settimeout(min(remaining, 8))
        completed = threading.Event()
        error: list[BaseException] = []

        def start_command() -> None:
            try:
                channel.exec_command("LC_ALL=C " + ("sh -c " + shlex.quote(command)))
            except BaseException as exc:  # surfaced to the caller below
                error.append(exc)
            finally:
                completed.set()

        threading.Thread(target=start_command, daemon=True).start()
        if not completed.wait(timeout=max(0.0, min(remaining, 8))):
            raise TimeoutError()
        if error:
            raise error[0]
        while time.monotonic() < deadline:
            if channel.recv_ready():
                chunk = channel.recv(32_768)
                if len(chunks) < 16_384:
                    chunks.extend(chunk[:16_384 - len(chunks)])
            elif channel.exit_status_ready():
                return channel.recv_exit_status(), chunks.decode("utf-8", errors="replace")
            else:
                time.sleep(0.05)
        raise TimeoutError()
    finally:
        channel.close()
