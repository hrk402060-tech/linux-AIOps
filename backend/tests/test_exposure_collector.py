from src.services.exposure_collector import COMMAND, parse_listeners


def test_listener_scope_does_not_claim_external_reachability() -> None:
    assert "ss -H -lntup" in COMMAND
    assert all(word not in COMMAND for word in ("nmap", "masscan", "iptables -A"))
    items = parse_listeners(
        'tcp LISTEN 0 128 0.0.0.0:22 users:(("sshd",pid=1,fd=3))\n'
        'tcp LISTEN 0 128 127.0.0.1:5432 users:(("postgres",pid=2,fd=4))\n'
    )
    assert items[0]["scope"] == "all_interfaces"
    assert "需结合防火墙" in str(items[0]["conclusion"])
    assert items[1]["scope"] == "loopback"

