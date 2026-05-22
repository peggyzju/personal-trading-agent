# Claude Code Instructions — Personal Trading Agent

## 基本规则

- 回复用**中文**
- 交易逻辑 / 信号 / 仓位 / 架构的大改动，**先出计划等确认，再写代码**
- 任何功能改动 commit 之前，必须运行测试并汇报结果（只有 ✅ 才能 commit）
- 测试命令：`python3 tests/e2e_daily.py --smoke`（smoke）或 `python3 tests/e2e_daily.py`（full）

## 后端重启

```bash
kill -9 $(lsof -ti:8000 -sTCP:LISTEN)
# 等 35 秒让 LaunchAgent 自动重启
```

**不能用** `pkill -f "python main.py"`（LaunchAgent 用大写 P Python，匹配不到）

## 当前策略版本：v3

### 选股（Scout · `src/monitor/sp500_scanner.py`）

双轨制过滤 `quick_screen()`：

| Track | 条件 |
|-------|------|
| Track 1 动能突破 | RSI 50–75（热板块升至 85）+ today_bull + mom5d > 0 + vs_ma20 ≤ 15% |
| Track 2 盘整蓄力 | RSI < 55 + volume_ratio < 0.8 + mom5d > −3%（bypass today_bull）|

**板块共振**：同板块 ≥ 3 只 today_bull → 该板块 Track 1 RSI 上限 75 → 85
- 常量：`SECTOR_RESONANCE_THRESHOLD = 3`，`SECTOR_RSI_BOOST = 10`

**Watchlist**（`watchlist.json`）：today_bull → 直接过；否则走 Track 2

### 买入（Rex · `src/trader/trade_agent.py`）

Entry Gate（任一不通过则跳过）：
- 非市场时间（9:25–16:05 ET Mon–Fri）→ 跳过所有买入
- 扫描信号非当日（跨日）→ 跳过
- 信号漂移 > 1.5%（`PRICE_DRIFT_THRESHOLD = 0.015`）→ 拒单
- 财报今天/明天未公布（`days=1`）→ 跳过（已公布放行）

止损（`src/analysis/position_sizer.py`）：
```
raw_stop = max(MA20 × 0.99, entry − 2×ATR)
stop = clamp(raw_stop, [entry×0.92, entry×0.97])   # −3% 到 −8%
```

### 卖出（Rex · `src/trader/trade_agent.py`）

| 机制 | 规则 |
|------|------|
| Alpaca Bracket GTC | 入场时挂止损单，−3% 至 −8%（ATR 决定），服务器端自动执行 |
| 追踪止盈 | `TRAIL_TRIGGER = 0.06`（+6% 激活）→ 高水位回落 `TRAIL_PCT = 0.05`（5%）触发 |
| Holdings Monitor 兜底 | `HARD_STOP_PCT = −8.0`：catch-all，Bracket 没触发时兜底 |
| AI 软清仓 | Claude 每 30 分钟评估持仓，SELL / REDUCE 信号 |
| Hold 冷却 | 连续 2 次 HOLD 才撤挂单；连续 2 次 REDUCE → 升级为 SELL |

## 关键参数（不要随意改动）

| 参数 | 值 | 文件 |
|------|----|------|
| `MIN_CASH_PCT` | 0.05 | trade_agent.py |
| `risk_pct` | 0.02 | trade_agent.py |
| `max_pos_pct` | 0.08 | trade_agent.py |
| `TRAIL_TRIGGER` | 0.06 | trade_agent.py |
| `TRAIL_PCT` | 0.05 | trade_agent.py |
| `PRICE_DRIFT_THRESHOLD` | 0.015 | trade_agent.py |
| `SECTOR_RESONANCE_THRESHOLD` | 3 | sp500_scanner.py |

## 版本管理

- `data/versions.json`：版本定义文件
- 只有**选股 / 买入 / 卖出逻辑变化**才定义新版本
- 前端「复盘」Tab 对比最近两个版本（v_prev vs v_current）
- 回测默认：6 个月，7 天持仓

## 架构速查

```
main.py (APScheduler)
  ├── 8:00 AM   Maya  — 市场 regime
  ├── 9:00 AM   Scout — Finviz 动态选股
  ├── 9:31 AM   Scout scan → Rex buy cascade
  ├── 12:30 PM  Scout scan → Rex buy cascade
  ├── every 30 min  Holdings refresh → Rex sell cascade
  ├── every 5 min   Fill sync
  └── 4:15 PM   Vera  — 收盘复盘

api/app.py (FastAPI :8000)    frontend/ (React+Vite :5173)
```

`_scan_running` 守卫必须在 `_run_sp500_scan()` 函数**最顶部**（任何 import 之前）。
