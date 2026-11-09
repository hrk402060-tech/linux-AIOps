from src.services.terminal_assistant import _local_analysis


def test_ssh_question_returns_targeted_read_only_diagnosis() -> None:
    diagnosis, plan, validation, commands = _local_analysis("怎么查看 ssh 服务器")

    assert "SSH" in diagnosis
    assert plan[0] == "确认 sshd 是否运行"
    assert validation
    assert any(command.startswith("systemctl status sshd") for command, _, _ in commands)
    assert commands[0][0] != "uptime"

def test_kubernetes_question_has_priority_over_ssh_context() -> None:
    diagnosis, plan, validation, commands = _local_analysis("安装 k8s\nsshd active")

    assert "Kubernetes" in diagnosis
    assert plan[0] == "确认发行版与内核"
    assert any(command.startswith("cat /etc/os-release") for command, _, _ in commands)
    assert not any(command.startswith("systemctl status sshd") for command, _, _ in commands)
