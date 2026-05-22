# Personal Trading Agent — Claude Code 指南

## 1. 项目目标

用 AI 驱动的自动化系统，在美股市场执行短线波段策略（持仓 5–10 天）：
- **选股**：从 ~700 只股票里每日筛出高胜率候选，捕捉动能突破和盘整蓄力两种形态
- **买入**：信号驱动，严格入场门控（市场时间、信号新鲜度、财报风险、追高保护）
- **卖出**：三层保护（Alpaca bracket 止损 + 追踪止盈 + AI 实时评估）
- **复盘**：每日收盘后 AI 分析胜负原因，提取策略迭代建议，注入下一轮扫描

目前运行在 **paper trading** 模式（Alpaca）。

---

## 2. Agent 设计

四个 AI Agent 协同运作，单一 APScheduler 调度：

| Agent | 职责 | 触发时间（ET）|
|-------|------|--------------|
| **Maya** | 读取市场 regime（牛/熊/震荡），设定当日仓位激进度和板块偏好 | 8:00 AM |
| **Scout** | 动态发现新标的（Finviz），扫描全量 universe，AI 评分候选股 | 9:00 AM + 9:31 AM + 12:30 PM |
| **Rex** | 读取 Scout 信号执行买入；监控持仓执行卖出 | 每次扫描后（买）+ 每 30 分钟（卖）|
| **Vera** | 收盘复盘，分析胜负特征，提取策略教训注入未来扫描 | 4:15 PM |

### 当前策略版本：v3

**选股（Scout）** — 双轨制 + 板块共振

| Track | 条件 |
|-------|------|
| Track 1 动能突破 | RSI 50–75（热板块升至 85）+ today_bull + mom5d > 0 + vs_ma20 ≤ 15% |
| Track 2 盘整蓄力 | RSI < 55 + vol_ratio < 0.8 + mom5d > −3%（bypass today_bull）|

板块共振：同板块 ≥ 3 只 today_bull → 该板块 RSI 上限 75 → 85

**买入（Rex）** — Entry Gate（任一不通过则跳过）

- 非市场时间（9:25–16:05 ET Mon–Fri）→ 跳过
- 扫描信号非当日 → 跳过
- 价格漂移 > 1.5% → 拒单
- 财报今天 / 明天未公布 → 跳过（已公布放行）
- 止损：`max(MA20×0.99, entry − 2×ATR)`，钳位 −3% 至 −8%

**卖出（Rex）** — 三层保护

| 层级 | 机制 |
|------|------|
| 1 | Alpaca Bracket GTC：入场时挂止损，服务器端自动执行，无需轮询 |
| 2 | 追踪止盈：+6% 激活，高水位回落 5% 触发（`TRAIL_TRIGGER=0.06, TRAIL_PCT=0.05`）|
| 3 | AI 软清仓：Claude 每 30 分钟评估持仓，SELL / REDUCE / HOLD 信号 |

Holdings Monitor `HARD_STOP_PCT = −8.0` 是最后兜底，不是主止损。

---

## 3. 基本规则

- 回复用**中文**
- 涉及交易逻辑 / 信号 / 仓位 / 架构的改动，**先出计划等确认，再写代码**
- 不添加用户没有要求的功能，不做过度抽象
- 不随意修改下表中的关键参数：

| 参数 | 值 | 文件 |
|------|----|------|
| `MIN_CASH_PCT` | 0.05 | trade_agent.py |
| `risk_pct` | 0.02 | trade_agent.py |
| `max_pos_pct` | 0.08 | trade_agent.py |
| `TRAIL_TRIGGER` | 0.06 | trade_agent.py |
| `TRAIL_PCT` | 0.05 | trade_agent.py |
| `PRICE_DRIFT_THRESHOLD` | 0.015 | trade_agent.py |
| `SECTOR_RESONANCE_THRESHOLD` | 3 | sp500_scanner.py |

---

## 4. 注意事项

**后端重启**
```bash
kill -9 $(lsof -ti:8000 -sTCP:LISTEN)
# 等 35 秒让 LaunchAgent 自动重启
```
不能用 `pkill -f "python main.py"`（LaunchAgent 使用大写 P Python，无法匹配）

**Race condition 守卫**
`api/app.py` 的 `_run_sp500_scan()` 中，`_scan_running` 守卫必须在函数**最顶部**（任何 import 之前），不能后移。

**硬止损架构（两层，勿混淆）**
- Alpaca Bracket GTC：入场时挂单，−3% 至 −8%（ATR 决定），服务器端自动触发
- `HARD_STOP_PCT = −8.0`：holdings monitor 兜底，仅在 bracket 未触发时生效
- e2e 测试 holdings monitor hard stop 必须用 ≤ −8% 的场景（如 −9%）

**版本定义**
只有选股 / 买入 / 卖出逻辑变化才在 `data/versions.json` 新增版本；前端只对比最近两个版本（v_prev vs v_current）。

**`BacktestView` 挂载**
`BacktestView.tsx` 必须在 `StrategyReview.tsx` 里 import + 挂载，放在 `components/` 目录不会自动生效。

---

## 5. 测试规则

- 任何功能改动 commit 之前，必须运行测试并汇报结果
- 只有 ✅ 才能 commit，❌ 必须先修复

```bash
# Smoke（必跑，约 30 秒）
python3 tests/e2e_daily.py --smoke

# Full（完整，约 2 分钟）
python3 tests/e2e_daily.py
```

测试覆盖范围：环境 / 账户 / Rex 核心逻辑 / Hard Stop + Trailing Stop / Market Regime / Scanner / Strategy Notes / 自主执行模式 / Vera 复盘 / 数据契约 / 调度器架构 / **v3 策略（双轨选股 + 板块共振 + 2×ATR止损 + 市场时间门）**
