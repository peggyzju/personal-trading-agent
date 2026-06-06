# 云迁移 Runbook — AWS Lightsail (us-east-1)

> 目标：把 personal-trading-agent 从会睡眠的笔记本搬到 **常开 Lightsail VM**，让 APScheduler 7×24 可靠触发。
> 选型：**AWS Lightsail $7/月**（1GB / 2vCPU / 40GB SSD / 2TB 流量），区域 **us-east-1**（离美股/Alpaca/Anthropic 最近）。
> 预计耗时：约半天。本机实现已确认：`main.py` 单进程起 APScheduler + uvicorn(0.0.0.0:8000)；密钥在 `.env`；依赖见 `requirements.txt`（8 个）。

---

## ⚠️ 割接铁律（先记住）

**Alpaca 账户同一时刻只能有一个实例在自动交易。** VM 上线那一刻，必须先停掉笔记本的 LaunchAgent，否则两边各自 cascade 下单 → 重复订单/互相打架。见 Phase 6。

---

## Phase 0 — 创建 Lightsail 实例

1. Lightsail 控制台 → **Create instance**
2. Region：**Virginia, us-east-1**
3. Platform：**Linux/Unix** → Blueprint：**OS Only → Ubuntu 22.04 LTS**
4. Plan：**$7/月**（1GB RAM）
5. 命名 `trading-agent` → Create
6. **Networking → 创建并挂载 Static IP**（挂着免费，避免重启换 IP）
7. **Firewall：保持默认只开 22(SSH)。绝不对公网开 8000**（交易看板裸奔公网很危险，看板走 Tailscale/SSH 隧道，见 Phase 5）
8. 下载默认 SSH key（或挂自己的）

```bash
# 本机连上去（IP = 你的 static IP）
chmod 600 ~/Downloads/LightsailDefaultKey-us-east-1.pem
ssh -i ~/Downloads/LightsailDefaultKey-us-east-1.pem ubuntu@<STATIC_IP>
```

## Phase 1 — 基础环境

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3 python3-venv python3-pip git rsync
# 可选：让日志时间好读（APScheduler 已写死 ET，不改时区也能正常工作）
sudo timedatectl set-timezone America/New_York
python3 --version   # Ubuntu 22.04 自带 3.10.x —— 所有依赖都支持，够用
```

## Phase 2 — 部署代码 + 状态 + 密钥

**代码**（二选一）：
```bash
# A. 有 git 远程（私有仓）→ clone
git clone <YOUR_REPO_URL> ~/personal-trading-agent

# B. 没远程 → 从本机 rsync 整个目录（排除体积大/会重建的）
#    在【本机】执行：
rsync -av --exclude '.venv' --exclude 'node_modules' --exclude 'frontend/dist' \
  -e "ssh -i ~/Downloads/LightsailDefaultKey-us-east-1.pem" \
  ~/personal-trading-agent/ ubuntu@<STATIC_IP>:~/personal-trading-agent/
```

**Python 依赖**：
```bash
cd ~/personal-trading-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**密钥**（`.env` 不进 git，单独拷，从【本机】执行）：
```bash
scp -i ~/Downloads/LightsailDefaultKey-us-east-1.pem \
  ~/personal-trading-agent/.env ubuntu@<STATIC_IP>:~/personal-trading-agent/.env
# VM 上收紧权限
ssh ... 'chmod 600 ~/personal-trading-agent/.env'
```
确认 `.env` 含：`ANTHROPIC_API_KEY` / `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` / `ALPACA_BASE_URL=https://paper-api.alpaca.markets`

**当前状态文件**（让 VM 从现状接力，而不是从零）。⚠️ 在割接当天、笔记本停跑后再做最后一次同步，避免状态错乱：
```bash
# 从【本机】执行
rsync -av -e "ssh -i ~/Downloads/LightsailDefaultKey-us-east-1.pem" \
  ~/personal-trading-agent/data/ ubuntu@<STATIC_IP>:~/personal-trading-agent/data/
```

**前端构建**（dist 是 gitignored，需在 VM 上build 或从本机拷）：
```bash
# VM 上构建（装 Node 一次）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt -y install nodejs
cd ~/personal-trading-agent/frontend && npm ci && npm run build
# 或：本机 build 好后 scp frontend/dist 过去（省得 VM 装 Node）
```

## Phase 3 — systemd 服务（替代 macOS LaunchAgent）

```bash
sudo tee /etc/systemd/system/trading-agent.service >/dev/null <<'EOF'
[Unit]
Description=Personal Trading Agent (APScheduler + FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/personal-trading-agent
ExecStart=/home/ubuntu/personal-trading-agent/.venv/bin/python main.py
Restart=always
RestartSec=10
# 日志交给 journald（别再写 1.3M 行的 backend.log）
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now trading-agent
systemctl status trading-agent --no-pager
journalctl -u trading-agent -f      # 看到 "[scheduler] Started" 即成功
```
> 对应关系：LaunchAgent `KeepAlive` → systemd `Restart=always`；`ThrottleInterval` → `RestartSec`。

## Phase 4 — 验证

```bash
cd ~/personal-trading-agent && source .venv/bin/activate
python tests/e2e_daily.py --smoke      # 期望 18/18
python tests/e2e_daily.py              # full
curl -s localhost:8000/api/market/regime    # 通 = Alpaca/SPY 可达
journalctl -u trading-agent | grep -i scheduler   # 确认 job 注册
```
盘中（9–15 ET）等一个 30 分钟 holdings tick 自然触发，或手动 `curl -XPOST localhost:8000/api/scan/holdings` 验证。

## Phase 5 — 看板访问（不暴露公网）

**推荐 Tailscale（免费）**：
```bash
# VM
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# 本机/手机也装 Tailscale 登同一账号 → 浏览器开 http://<vm-tailscale-ip>:8000
```
**或 SSH 隧道**（零安装）：
```bash
ssh -i key -L 8000:localhost:8000 ubuntu@<STATIC_IP>   # 然后本机开 http://localhost:8000
```
两种都不需要在 Lightsail 防火墙开 8000。

## Phase 6 — 割接 + 收尾

**① 停掉笔记本实例（铁律！）**：
```bash
# 在【本机 macOS】执行
launchctl unload ~/Library/LaunchAgents/com.trading-agent.backend.plist
kill -9 $(lsof -ti:8000 -sTCP:LISTEN) 2>/dev/null
```
确认本机不再自动跑后，再做 Phase 2 的 data/ 最后一次同步 → 启 VM 服务。**任何时刻只有一边在交易。**

**② 数据备份 cron**（data/ 是全部家当）：
```bash
mkdir -p ~/backups
( crontab -l 2>/dev/null; echo '0 5 * * * tar czf ~/backups/data-$(date +\%F).tgz -C ~/personal-trading-agent data && find ~/backups -name "data-*.tgz" -mtime +14 -delete' ) | crontab -
```

**③ 日志**：已交给 journald（自动滚动），不会再像本机 backend.log 涨到百万行。

---

## 成本小结

| 项 | 费用 |
|---|---|
| Lightsail $7 实例（含 2TB 流量、static IP 挂载免费） | **$7/月** |
| Tailscale 个人版 | $0 |
| 备份（本地 tar，可选推 S3） | ~$0 |
| **合计** | **≈ $7/月** |

## 迁后观察项

- **yfinance（残留 11 处：regime/sector/news/enrich）**：数据中心 IP 被 Yahoo 限流的特征跟住宅 IP 不同（可能更好也可能更差）。关键路径已在迁 Alpaca；迁后看一周扫描质量，必要时把剩余 yfinance 也切 Alpaca。
- **首日盯一次**：确认 8:00 Maya / 8:45 Scout / 9:31 首扫 / 每 30 分钟 holdings 全部按 ET 自动触发（这正是搬云要解决的核心）。
