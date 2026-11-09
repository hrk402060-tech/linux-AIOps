import { Button, Collapse } from 'antd';
import { ArrowRight, ShieldCheck } from 'lucide-react';
import { CommandBlock } from './user-docs/CommandBlock';

const sshGateway = `# 由目标服务器管理员在项目目录执行，需先准备专用账号 opsdiag
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 deploy/diagnostics/linux_ops_readonly.py \\
  /usr/local/libexec/linux_ops_readonly.py

# 专用公钥限制行；PUBLIC_KEY 替换为诊断公钥
restrict,command="/usr/bin/python3 -I /usr/local/libexec/linux_ops_readonly.py" ssh-ed25519 PUBLIC_KEY`;

const sshChecks = `# 由管理员在持有专用密钥的机器执行
# 首次连接自动接受主机密钥；替换地址和私钥路径
ssh -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -o BatchMode=yes \\
  -i ./opsdiag opsdiag@SERVER_IP \\
  linuxops-readonly-v1:handshake
ssh -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -o BatchMode=yes \\
  -i ./opsdiag opsdiag@SERVER_IP \\
  linuxops-readonly-v1:linux.uptime`;

const dockerReads = `GET /v1.45/containers/json?all=true&limit=50
GET /v1.45/containers/CONTAINER_ID/stats?stream=false&one-shot=true`;

const kubernetesRbac = `# 由集群管理员应用；ops、production 须已存在
apiVersion: v1
kind: ServiceAccount
metadata:
  name: opsdiag
  namespace: ops
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: linux-ops-readonly
  namespace: production
rules:
  - apiGroups: [""]
    resources: ["pods", "events"]
    verbs: ["list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets"]
    verbs: ["list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: linux-ops-readonly
  namespace: production
subjects:
  - kind: ServiceAccount
    name: opsdiag
    namespace: ops
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: linux-ops-readonly`;

const kubernetesChecks = `# 保存上面的清单为 readonly-rbac.yaml 后，由集群管理员执行
kubectl apply -f readonly-rbac.yaml
kubectl -n ops create token opsdiag --duration=2h

# 使用有模拟身份权限的管理员上下文核查，预期依次为 yes / no / no
kubectl auth can-i list pods -n production --as=system:serviceaccount:ops:opsdiag
kubectl auth can-i get secrets -n production --as=system:serviceaccount:ops:opsdiag
kubectl auth can-i delete pods -n production --as=system:serviceaccount:ops:opsdiag`;

const mysqlChecks = `# 目标管理员用专用账号核验；按实际证书和地址替换
mysql --ssl-mode=VERIFY_IDENTITY --ssl-ca=CA.pem -h SERVER_IP -P 3306 \\
  -u opsdiag -p -e "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Threads_created','Slow_queries','Uptime'); SHOW GLOBAL VARIABLES WHERE Variable_name IN ('version','read_only','super_read_only','innodb_buffer_pool_size','max_connections'); SHOW REPLICA STATUS\\G"
# 仅当服务器不支持 SHOW REPLICA STATUS 时，将其替换为 SHOW SLAVE STATUS
# 仅核验只读账号能访问允许的状态查询；禁止任意 SQL`;

const redisChecks = `# ACL 用户、TLS/CA 和 DB 范围由目标管理员准备
# 每条命令交互输入专用密码，按实际 DB 修改 -n
redis-cli --tls --cacert CA.pem -h SERVER_IP -p 6379 \\
  --user opsdiag --askpass -n 0 INFO server
redis-cli --tls --cacert CA.pem -h SERVER_IP -p 6379 \\
  --user opsdiag --askpass -n 0 INFO memory
redis-cli --tls --cacert CA.pem -h SERVER_IP -p 6379 \\
  --user opsdiag --askpass -n 0 INFO clients
redis-cli --tls --cacert CA.pem -h SERVER_IP -p 6379 \\
  --user opsdiag --askpass -n 0 SLOWLOG GET 16
# 不执行 SET、DEL、CONFIG、FLUSH、EVAL 或其他写操作`;

const rabbitmqChecks = `# 管理 HTTPS 15671、CA、vhost 和专用非 guest/admin 用户由管理员准备
curl --fail --max-time 10 --cacert CA.pem --user opsdiag \\
  https://SERVER_IP:15671/api/overview
curl --fail --max-time 10 --cacert CA.pem --user opsdiag \\
  https://SERVER_IP:15671/api/nodes
# 仅 GET；按 vhost 查询 queues/consumers，禁止 publish、consume 和写请求`;

function SshHelp() {
  return <div className="help-diagnostic-detail">
    <ol>
      <li>目标管理员准备非 root 的专用账号、独立诊断密钥和 Python 3；不给 sudo、docker 组、系统配置写权限或应用密钥访问权限。</li>
      <li>将项目中的限制网关安装到目标服务器。网关及父目录须由 root 所有，普通账号不可修改；安装命令仅由管理员执行。</li>
    </ol>
    <CommandBlock title="安装 SSH 限制网关" text={sshGateway} />
    <p>将限制行写入 root 管理的公钥文件，例如 /etc/ssh/diagnostic_keys/opsdiag，目录 0755、文件 0644；
      由管理员在 sshd 中为此账号指定该文件，禁用密码及键盘交互认证、终端、SFTP、转发、隧道和用户启动脚本。
      使用 sshd -t 检查后按目标发行版流程生效，并独立验证这些入口确实被拒绝。</p>
    <p>在平台选择 Linux 类型，填写专用账号、诊断私钥、允许证据和有效期；账号必须与人工 SSH 账号不同。
      目标主机密钥由平台首次连接时自动接受并仅在本次连接内存中使用；当前仅支持固定 IP，不使用密码或人工 SSH 凭据回退。</p>
    <p>手工验证机器可使用 StrictHostKeyChecking=accept-new；平台不会把主机密钥写入浏览器或资产页面；
      非 22 端口在下列命令添加 -p PORT。当前诊断私钥不支持口令加密，须使用独立受保护的诊断密钥。</p>
    <CommandBlock title="验证 SSH 只读查询" text={sshChecks} />
    <p>仅支持负载与运行时间、内存、磁盘、inode、失败服务和进程资源六类固定查询；
      不接收自由命令，不读取任意文件、进程完整命令行或环境变量。握手成功不代表目标权限已正确收敛。</p>
  </div>;
}

function DockerHelp() {
  return <div className="help-diagnostic-detail">
    <ol>
      <li>管理员先准备专用 HTTPS 受限代理，配置独立 Bearer Token、可信 CA 和仅匹配下列请求的白名单。
        当前项目未提供 Docker 代理安装器，需由管理员另行部署并验证拒绝规则。</li>
      <li>在平台选择 Docker 类型，填写代理地址、代理身份、代理 Token、CA PEM、采集范围和有效期。
        地址必须为与资产 IP 相同的 HTTPS 地址，证书须包含该 IP 的 SAN。</li>
      <li>仅允许容器列表，以及本次列表已发现、运行中容器的单次资源快照；容器 ID 必须是完整的 64 位十六进制 ID。</li>
    </ol>
    <CommandBlock title="Docker 允许的只读请求" text={dockerReads} />
    <p>不能直接开放 2375、加入 docker 组或把 docker.sock 挂载为只读就认定安全；
      代理必须拒绝未批准路径、查询参数、所有写请求，以及 exec、inspect、日志、重启、删除、镜像和文件接口。</p>
    <p>先独立验证代理的认证、TLS 和拒绝行为，再在平台保存授权并执行验证；平台不会替你安装代理或修改 Docker 配置。</p>
  </div>;
}

function KubernetesHelp() {
  return <div className="help-diagnostic-detail">
    <p>由集群管理员准备独立 ServiceAccount 和命名空间级 Role / RoleBinding。以下清单仅为已存在的
      ops、production 命名空间示例；按所选范围收窄资源，在每个需要的命名空间单独绑定，不授予集群管理员角色。</p>
    <CommandBlock title="K8s 最小读取权限清单" text={kubernetesRbac} />
    <CommandBlock title="验证 K8s 读取权限" text={kubernetesChecks} />
    <p>在平台选择 Kubernetes 类型，填写 API Server 的固定 IP HTTPS 地址、ServiceAccount 名称和所在命名空间、
      CA PEM、Token，以及最多 3 个命名空间和采集范围。目标 IP 必须与映射资产一致，证书须有该 IP 的 SAN。</p>
    <p>Token 实际到期时间必须覆盖平台授权；上例申请 2 小时，实际时长由集群决定，平台有效期应更短。
      过期后由管理员重新申请并更新。Token 只能提交授权表单，不要放进 AI 对话或日志。</p>
    <p>仅查询所选命名空间的 Pod、工作负载和事件。当前不采集节点、日志、Secrets、ConfigMap、metrics，
      不开放 exec、proxy、watch 或任何写操作。勾选确认不会自动创建 RBAC；还需排查该身份是否继承了其他授权。</p>
  </div>;
}

function ServiceReadOnlyHelp({ kind, description, checks }: {
  kind: string; description: string; checks: string;
}) {
  return <div className="help-diagnostic-detail">
    <p>{description}</p>
    <CommandBlock title={`${kind} 人工只读核验`} text={checks} />
    <p>先由目标管理员在服务侧核验 TLS、证书、专用账号和权限范围，再在平台保存授权并执行验证。
      平台只调用已选的固定读取动作，不接收任意 SQL、Redis 命令或 RabbitMQ 写请求；{kind} 凭据不会回传明文。</p>
    <p>上述手工查询可能返回原始字段或慢查询内容，仅由目标管理员本地核验，不粘贴到 AI 或工单；
      平台采集会按固定字段投影，Redis 慢日志仅保留数量、ID 和耗时。</p>
  </div>;
}

export function ReadOnlyDiagnosticsHelp({ onOpenAlerts, embedded = false }: {
  onOpenAlerts: () => void; embedded?: boolean;
}) {
  return <section className={embedded ? 'help-diagnostic-detail' : 'help-section'}>
    {!embedded && <div className="help-section-heading"><ShieldCheck size={18} /><h2>只读诊断授权与执行</h2></div>}
    <p className="help-diagnostic-location">监控告警 → 活动告警 → 只读诊断授权</p>
    <ol>
      <li>先由目标管理员准备对应的只读身份和限制，再由平台管理员点击“新增只读授权”。
        平台管理员可配置、验证和撤销授权；操作员和审计员不能代替管理员授权。</li>
      <li>选择已启用资产、连接类型和最小采集范围，填写原因、资产名称确认及有效期（最长 90 天）。
        同一资产仅能配置一种诊断类型；Docker / K8s 使用未绑定人工 SSH 凭据的独立映射资产。</li>
      <li>保存后点击该行“验证只读授权”，输入资产名称和验证原因。验证会真实采集所选只读证据并留下审计，
        部分失败或失败需查看原因，不能将验证完成等同于服务健康或故障恢复。</li>
      <li>进入“外部告警研判”，关联告警与资产，在详情人工提交只读诊断并查看任务、证据和结果。
        “管理员通知”需要另外配置 SMTP 和收件人，确认实际投递，不能把通知入队当作发送成功。</li>
    </ol>
    <Collapse accordion size="small" items={[
      { key: 'ssh', label: 'SSH（Linux）只读查询', children: <SshHelp /> },
      { key: 'docker', label: 'Docker 只读查询', children: <DockerHelp /> },
      { key: 'kubernetes', label: 'K8s 只读查询', children: <KubernetesHelp /> },
      { key: 'mysql', label: 'MySQL 只读查询', children: <ServiceReadOnlyHelp kind="MySQL"
        description="使用非管理员账号、TLS/CA 和 3306 端口；平台固定读取状态、变量和复制状态，不读取业务表。" checks={mysqlChecks} /> },
      { key: 'redis', label: 'Redis 只读查询', children: <ServiceReadOnlyHelp kind="Redis"
        description="使用 ACL 非 root/admin 用户、TLS/CA、6379 端口和限定 DB（0–15）；平台读取 server、memory、clients 与 16 条慢日志。" checks={redisChecks} /> },
      { key: 'rabbitmq', label: 'RabbitMQ 只读查询', children: <ServiceReadOnlyHelp kind="RabbitMQ"
        description="使用管理 HTTPS 15671、CA、限定 vhost 和非 guest/admin 用户；平台只读取 overview、nodes、queues 与 consumers。" checks={rabbitmqChecks} /> },
    ]} />
    <p>以上配置命令由目标管理员手动执行，帮助页不会执行命令或连接服务器。
      人工 SSH 权限由资产登录账号决定；AI 诊断只调用授权的固定只读动作，人工确认也不会把诊断授权升级为写权限。
      撤销、到期或目标变更后停止后续读取，历史证据保留；密钥和 Token 保存后不回传明文。</p>
    <Button icon={<ArrowRight size={15} />} onClick={onOpenAlerts}>打开活动告警</Button>
  </section>;
}
