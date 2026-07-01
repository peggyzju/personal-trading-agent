# Personal Trading Agent — Claude Code 指南

## 1. 项目目标

用 AI 驱动的自动化系统，在美股市场执行短线波段策略（持仓 5–10 天）：
- **选股**：从 ~700 只股票里每日筛出高胜率候选，捕捉动能突破和盘整蓄力两种形态
- **买入**：信号驱动，严格入场门控（市场时间、信号新鲜度、财报风险、追高保护）
- **卖出**：v8 纯机械退出（−8% 止损 + 追踪止盈 +6%/−8% + MA20 破位；无 AI 卖出）
- **复盘**：每日收盘后 AI 分析胜负原因，提取策略迭代建议，注入下一轮扫描

目前运行在 **paper trading** 模式（Alpaca）。

---

## 2. Agent 设计

四个 AI Agent 协同运作，单一 APScheduler 调度：

| Agent | 职责 | 触发时间（ET）|
|-------|------|--------------|
| **Maya** | 读取市场 regime（牛/熊/震荡），设定当日仓位激进度和板块偏好 | 8:00 AM |
| **Scout** | 选股：盘前动态发现新标的（Finviz）+ 日内扫描全量 universe + AI 评分候选股 | 8:45 AM（发现）+ 9:31 / 11:00 / 12:30 / 14:30（扫描）|
| **Rex** | 交易执行：读取 Scout 信号执行买入；监控持仓执行卖出 | 每次扫描后 cascade（买）+ 每 30 分钟（卖）|
| **Vera** | 收盘复盘，分析胜负特征，提取策略教训注入未来扫描 | 手动 trigger（POST /api/strategy/review，已移除自动定时）|

### 当前策略版本：v8（趋势统一 — 2026-06-29）

> v8 把整套系统统一到**单一趋势/动量 thesis**,消除 v7 的内在矛盾(Track1 追强 vs Track2 抄底)。
> 回测+稳健性:9/9 组参数均赢 SPY(+150% vs +100%,回撤相当,见 `scripts/v8_robustness.py`)。
> 根因:实盘 PF 0.67、32% 胜率(选股矛盾)+ 赢家被砍短。

**选股(Scout · 机械动量,无 AI)** — 砍掉双轨,只买"上升趋势中的强势股"
- price > MA50 且 **MA50 上升**(5日斜率>0)
- RSI **50–80**(强势区,不再抄底超卖)
- 3 月动量 > 0(≈60日动量)
- vs_ma20 ≤ 15%(不过度延伸)
- **按 3 月动量排名**取 top N(替代 tech_score / ai_score 排序)

**AI 评分** — 从买入主路**撤下**:不再 min_ai_score / SELL-HOLD 门控。降为可选"排雷"(A/B 验证后才生效)。AI 仍用于:财报研判、收盘复盘。

> v1–v7 历史见 `data/versions.json`。v7 双轨(Track1 动能 + Track2 盘整 + 板块共振)已被 v8 取代。

**买入（Rex）— v8 纯机械** — Entry Gate（任一不通过则跳过）

- **候选来源 = 扫描 top-N ∪ 自选(watchlist)**，两者都必须过同一道趋势门；自选(force_symbols)唯一特殊 = 过门后不被 top-N 截断，**无买入优先权**（仍按 `momentum_3m` 排名）
- **槽位上限**：`可买 = max(0, regime.max_positions − 持仓 − 已pending买单)`，买入循环内**逐个递减、满即 break**（`_slots_remaining()`）。已 pending 也占位——否则两次扫描叠加超额（6-30 暴冲根因，勿回退）
- 非市场时间（9:30–16:00 ET Mon–Fri）→ 跳过
- 扫描信号非当日 → 跳过
- 价格漂移（执行端 `approve_trade` / `_price_drift_decision()`）：现价比扫描价**涨 > 1.5%** → 拒单（追高失效）；**跌了或阈内** → 放行（更好入场）。`PRICE_DRIFT_THRESHOLD=0.015`
- 财报今天 / 明天未公布 → 跳过（已公布放行）
- **止损：固定 −8%**（`round(price×0.92, 2)`;止损门容差 8.5%,真正过宽才挡）
- **regime 门控（Maya）**：BEAR → block_buys 全停;CAUTION 等 → 只缩放 `size_factor`、压缩 `max_positions`。**不再有 AI 分门（min_ai_score）、Gate A、Gate B/R:R**。
- **熔断**：单日组合亏损 ≤ −5% → 当天封锁所有买入（只留卖出），次日复位（`circuit_breaker.py`）
- **WSB 极端热度**：候选 `hype_label=="extreme"` → 仓位砍半（追高保险，只缩仓不改买卖决策）
- **自动 / 人工**：默认**自动执行**(confidence 固定 0.8,无分数概念;自动审批为**纯开关**,无阈值);唯一例外 = **AI 排雷 veto=True → 进人工审核队列**。**veto 待审 2h 未处理 → 自动作废、释放槽位补位**（`expires_at=created+2h`）
- **下单**：Alpaca bracket（市价入场 + −8% 止损），**时效 GTC**（子止损单跨日持续有效，勿用 day——会当天 expired 裸奔）

> v5 的 Gate A(SPY>MA20)、Gate B(R:R≥1.5)、v7 的 min_ai_score 门控 / 2×ATR 结构化止损 均已在 v8 移除。

**卖出（Rex）— v8 纯机械退出（无 AI 卖出，对齐回测）**

| 层级 | 机制 |
|------|------|
| 1 | **初始止损 −8%**：Alpaca Bracket **GTC**，入场即挂，服务器端自动执行 + holdings monitor `HARD_STOP_PCT=-8` 每30分钟兜底 |
| 2 | **追踪止盈**：浮盈高水位 +6% 激活，高水位回落 8% 触发（`TRAIL_ACTIVATE_PCT=6.0, TRAIL_PCT=8.0`，holdings_monitor）|
| 3 | **MA20 破位**：**连续 2 根**日线收盘 < MA20 → SELL（`ma20_below_2d`；回测验证优于单根：收益+7pt、卖出−39%，过滤单根假破位/健康回调）|

- 三者都是机械规则（holdings_monitor `_rule_based_override`）：硬止损 > 追踪止盈 > MA20 破位。主动卖出前先取消保护止损单再 `close_position`。
- **让赢家跑 = 追踪止盈**（+6% 才激活、回撤 8% 才走);追踪未激活时由 -8% 硬止损兜底。
- **超配再平衡（保证金保护,非策略性卖出）**：持仓市值 > 权益 95% → 卖最差盈亏直到降回 90%（`OVERALLOC_THRESHOLD=0.95→0.90`）。这是**唯一一条不按上面 3 规则的卖出**,只为防杠杆。
- **v8 已撤掉 v7 的「AI 软清仓」+「趋势过滤(REDUCE 压制)」死逻辑** —— 回测验证的是纯机械退出,AI/REDUCE 不再参与卖出。

> 完整版本历史见 `data/versions.json`（v1–v7）。注意：v6 → v7 的回测结果必然相同——v7 是 regime/AI-score 实盘门控，机械回测引擎读不到这些参数，只能看实盘 Track 数据。

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
| `TRAIL_TRIGGER` | 0.06 (v8,原0.10) | trade_agent.py |
| `TRAIL_PCT` | 0.08 (v8,原0.05) | trade_agent.py |
| `PRICE_DRIFT_THRESHOLD` | 0.015 | trade_agent.py |
| `SECTOR_RESONANCE_THRESHOLD` | 3 | sp500_scanner.py |

---

## 4. 注意事项

### 后端重启
```bash
kill -9 $(lsof -ti:8000 -sTCP:LISTEN)
# 等 35 秒让 LaunchAgent 自动重启
```
- ❌ 不能用 `pkill -f "python main.py"`（LaunchAgent 用大写 P Python，匹配不到）
- ❌ 不能用 `--no-verify` 跳过 hook

### 代码改动生效
- 改完 `.py` 文件必须重启后端，Python 模块缓存不会自动热更新
- 改完前端 `.tsx` / `.css` Vite HMR 会自动更新，但大改动建议手动刷新浏览器

### Race Condition
- `api/app.py` → `_run_sp500_scan()`：`_scan_running = True` 守卫必须在函数**最顶部**，任何 slow import 之前。移到后面会留下 ~5 秒窗口导致重复触发。

### 止损两层架构（勿混淆）

| 层级 | 机制 | 触发阈值 | 执行方 |
|------|------|---------|--------|
| 主止损 | Alpaca Bracket GTC | 固定 −8%（`price×0.92`）| Alpaca 服务器，毫秒级，无需轮询 |
| 兜底止损 | Holdings Monitor `HARD_STOP_PCT` | −8.0% | holdings refresh，每 30 分钟 |

> e2e 测试 holdings monitor hard stop 必须用 ≤ −8% 场景（如 −9%），用 −3% 测不会触发。

### 版本管理
- 只有**选股 / 买入 / 卖出逻辑**变化才在 `data/versions.json` 新增版本
- 纯 UI、参数微调、bug fix 不算新版本
- 前端「复盘」Tab 永远只对比最近两个版本（v_prev vs v_current）

### 前端组件挂载
- 新建 `components/Xxx.tsx` 不会自动出现在页面，必须在对应父组件里 import + 渲染
- 例：`BacktestView.tsx` 需在 `StrategyReview.tsx` 里挂载

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

测试覆盖范围：环境 / 账户 / Rex 核心逻辑 / Hard Stop + Trailing Stop / Market Regime / Scanner / Strategy Notes / 自主执行模式（纯开关）/ Vera 复盘 / 数据契约 / 调度器架构 / **v8 机械规则(smoke 也跑,守根因)：槽位上限 `_slots_remaining`（防超额）· Bracket 止损 = GTC（防当天过期）· MA20 连续2根破位 · 价格漂移门 `_price_drift_decision`（涨拒跌放）· veto 2h TTL**

> 新增 v8 机械规则测试(`test_slot_cap` / `test_bracket_gtc` / `test_ma20_exit` / `test_price_drift_gate` / `test_veto_ttl`)在 smoke+full 都跑 —— 直接守住 6-30 暴冲、止损裸奔等实盘根因。改买卖逻辑必须先让这些绿。
