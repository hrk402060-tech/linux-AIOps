# Alertmanager 签名中继

平台告警接收接口要求每个请求带动态 HMAC。Alertmanager 原生 webhook 不生成该签名，因此中继逐条拆分批量告警，再转发到平台的 `alert-ingest` 地址。

设置以下服务端变量：

```text
ALERT_RELAY_TARGET_URL=http://backend:8000/api/v1/alert-ingest/<connector-id>
ALERT_RELAY_SECRET=<连接器创建后只显示一次的密钥>
ALERT_RELAY_LISTEN_HOST=0.0.0.0
ALERT_RELAY_LISTEN_PORT=8080
```

Alertmanager webhook 指向 `http://alert-relay:8080/alertmanager`。中继保留 `fingerprint`、`instance`、labels 和 annotations，并逐条转发 `firing` 与 `resolved` 事件；平台会把恢复事件记录为源恢复信号，仍要求人工验证后关闭告警。

容器只需要访问平台告警接收地址，不挂载 Docker socket，不读取 SSH 或 Kubernetes 凭据。连接器密钥只放在服务端环境变量或密钥管理器中。
