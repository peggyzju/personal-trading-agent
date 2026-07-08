# Personal Trading Agent — Codex 指南

> 当前策略版本 **v13（Quality Momentum 排序 + v12 EF5 + v11 soft overlay，2026-07-08）**。历史版本见 `data/versions.json`。

---

## 1. 目标

用**机械动量**策略在美股做**短线波段**（持仓 5–10 天），Alpaca **paper trading**。全自动执行，人在环监督/否决。

- **选股**：机械动量（趋势门 → quality_momentum 排序），无 AI；AI 链板块轮动只做 soft overlay 排序
- **买入**：机械入场门 + 默认自动执行；唯一 AI 参与 = 买入侧**排雷**层，把高风险票转人工审核
- **卖出**：纯机械退出（−8% 硬止损 + EF5 新仓失败止损 + BE5 保本 + 追踪止盈 + MA20 破位）

---

## 2. Agent 执行机制 + 每个 Agent 策略

### 执行机制
- 单一 APScheduler 调度，**Maya / Scout / Rex** 三个 Agent 协同，全程**美东时间 ET**。
- 收盘**复盘为手动触发**（`POST /api/strategy/review`），非自动、当前不影响买卖。
- **两层止损架构（勿混淆）**：

  | 层 | 机制 | 阈值 | 执行方 |
  |---|---|---|---|
  | 主止损 | Alpaca Bracket **GTC**（入场即挂） | −8%（`price×0.92`） | Alpaca 服务端，毫秒级 |
  | 兜底 | Holdings Monitor `HARD_STOP_PCT` | −8% | 每 30 分钟 refresh |

  > Bracket 时效**必须 GTC** —— 用 `day` 会当天收盘 expired，仓位裸奔（`alpaca_trader.place_order`）。

### Maya — 市场 regime
- **数据**：SPY 相对 MA5 / MA20 / MA50 + 日内涨跌
- **时间**：8:00 ET
- **策略**：判 regime → 定当日仓位上限 + 缩放 + 是否封锁买入（切换有迟滞，连续确认防抖动）

  | 档 | 判定（SPY） | max_positions | size_factor | block_buys |
  |---|---|---|---|---|
  | BULL | 站上 MA5/20/50 | 10 | 1.0 | 否 |
  | NEUTRAL | 站上 MA20 但混合 | 7 | 0.75 | 否 |
  | CAUTION | 破 MA5 或日内跌 >1.5% | 5 | 0.5 | 否 |
  | BEAR | 破 MA20 | 3 | 0.0 | 是（只管止损） |

### Scout — 选股（机械动量 + Quality Momentum 排序 + AI链 soft rotation overlay，无 AI 个股选择）
- **数据**：~572 只 universe（S&P500 + 纳指100 + Layer2）的 **Alpaca 批量日线 bars**：完成日线算结构/排名，盘中未完成日线算当前确认
- **时间**：8:45 盘前发现（Finviz）+ 9:31 / 11:00 / 12:30 / 14:30 扫描
- **策略**：**上日结构门 + 盘中确认门 → 按 `quality_momentum_score` 排名；必要时启用 AI链软件接棒 soft overlay**
  - 上日结构：上日收盘 > MA50 · MA50 上升（5日斜率>0）· 上日 3月动量>0
  - 盘中确认：当前价 > 上日 MA50 · 当前 RSI 50–80 · 当前 3月动量>0 · 当前 vs 上日 MA20 ≤ 15%
  - 默认过门后按 `quality_momentum_score` 降序取 top N：3M 40% · 1M 25% · 5D 15% · MA50斜率 10% · MA20位置质量 5% · RSI质量 3% · 量能质量 2%
  - 候选 `price` 用盘中当前价供 Rex 仓位/止损；自选（force_symbols）**走同一道门、不特殊、无买入优先权**
  - **AI链软件接棒 soft overlay（v13 继承 v11）**：触发条件 = 软件 3日中位回报 − 硬件 3日中位回报 > 3% · 硬件 1日中位回报 < −2% · 软件站上 MA20 breadth > 硬件 breadth
  - overlay 触发后：排序桶为 software → other → hardware；桶内仍按 `quality_momentum_score`；硬件需 RSI ≥ 52 且当前价 ≥ MA20；top10 软件最多 6、硬件最多 3（top25 软件最多 12、硬件最多 8）
  - overlay 不是硬件禁买，也不影响已有持仓卖出；只影响新增候选的排序/截断
  - **动量窗口 = 3mo 的决策**：稳健性 9/9 赢 SPY；120d 纯回测更高但仍选 3mo（匹配 5–10 天持仓 + 防幸存者偏差）。**要换先做去幸存者偏差重测**
  - 个股过门仍全机械；AI 不做个股选择（AI 仅在买入侧做排雷 veto，见下）

### Rex — 买入（v13 沿用机械买入）
- **数据**：Scout 候选（含扫描价）· Alpaca 实时价（漂移/执行）· Finnhub 财报 · regime · 熔断状态 · 当前持仓 + pending
- **时间**：每次扫描后 cascade
- **策略**：候选 = 扫描 top-N ∪ 自选（同门）→ 逐个过入场门 → 自动执行
  - **槽位上限**：`max(0, regime.max_positions − 持仓 − 已pending买单)`，买入循环内**逐个递减、满即 break**（`_slots_remaining()`）。pending 占位，防两次扫描叠加超额（6-30 暴冲根因，勿回退）
  - 市场时间 9:30–16:00 ET · 扫描信号非当日跳过 · 财报今/明未公布跳过（已公布放行）
  - **价格漂移门**（`_price_drift_decision`）：现价比扫描价**涨 >1.5% 拒单**（追高失效）；跌/阈内放行（更好入场）
  - 止损固定 −8%（`round(price×0.92,2)`，容差 8.5%）· regime 门（BEAR 全停）· **熔断**（单日组合 ≤−5% 当天停买）· **WSB extreme** 仓位砍半
  - 仓位：risk 2%/单，单仓 ≤8%，现金留 ≥5%，按 `size_factor` 缩放
  - **自动/人工**：`auto_approve.enabled=true` 时自动（confidence 固定 0.8；自动审批**纯开关**无阈值；配置缺失/损坏时 fail-closed=关闭）；`veto=True` → 人工队列，**2h 未处理自动作废、释放槽位补位**（`expires_at=created+2h`）
  - **下单**：Alpaca bracket（市价入场 + −8% 止损），时效 **GTC**

### Rex — 卖出（v13 继承 v12 纯机械退出，无 AI，每 30 分钟）
- **数据**：持仓（Alpaca）· 实时价 · 最近一次 buy 入场时间 · 日线（算 MA20/RSI）· `trailing_stops.json` 高水位
- **时间**：≥9:31 起每 30 分钟
- **策略**：`_rule_based_override` 优先级 **硬止损 > EF5 新仓失败止损 > 追踪止盈 > 保本退出 > MA20 破位**
  1. **硬止损**：plpc ≤ −8%
  2. **EF5 新仓失败止损**：D1-D2 内，浮亏 ≤ −5% 退出；D0 不触发（避免入场前日内低点干扰）
  3. **追踪止盈**：高水位 +6% 激活 → 从高点回撤 5% 触发（`TRAIL_ACTIVATE_PCT=6.0 / TRAIL_PCT=5.0`）
  4. **保本退出**：高水位 +5% 激活 → 价格回到实际持仓均价附近触发（`BREAKEVEN_ACTIVATE_PCT=5.0`）
  5. **MA20 破位**：**连续 2 根**日线收盘 < MA20（`ma20_below_2d`，过滤单根假破位/健康回调）
  - 主动卖出前先**取消保护止损单**再 `close_position`
  - **超配保护（非策略性卖出）**：持仓市值 > 权益 95% → 卖最差盈亏直到降回 90%（防杠杆）

### 关键参数（勿随意改）
`MIN_CASH_PCT=0.05` · `risk_pct=0.02` · `max_pos_pct=0.08` · 止损固定 8% · `EARLY_FAILURE_STOP_PCT=-5.0`(D1-D2) · `BREAKEVEN_ACTIVATE_PCT=5.0` · `TRAIL_ACTIVATE_PCT=6.0`/`TRAIL_PCT=5.0`(holdings_monitor) · `PRICE_DRIFT_THRESHOLD=0.015` · veto TTL 2h

---

## 3. 测试

- 任何功能改动 commit **之前必须跑测试并汇报**，只有 ✅ 才 commit，❌ 先修。
- 改**买卖逻辑**必须先让下面「v8/v9 机械规则」测试全绿。

```bash
python3 tests/e2e_daily.py --smoke   # smoke（必跑，~30s）
python3 tests/e2e_daily.py           # full（~2min）
```

**覆盖**：环境 / 账户 / Market Regime / Scanner / Strategy Notes / 自主模式（纯开关）/ 手动复盘 / 数据契约 / 调度器 · **v8/v9 机械规则（smoke 也跑，守实盘根因）**：
- `test_slot_cap` 槽位上限（防超额，6-30 暴冲）· `test_bracket_gtc` bracket=GTC（防裸奔）
- `test_ma20_exit` MA20 连续2根破位 · `test_price_drift_gate` 漂移门（涨拒跌放）· `test_veto_ttl` veto 2h
- `test_hard_stop_logic` 硬止损 + 保本退出 + 追踪止盈（hard stop 场景须用 ≤−8%，如 −9%）· `test_early_failure_stop` EF5 D1-D2

---

## 4. Coding 规则

- 回复用**中文**；一律用**美东时间 ET**对话。
- **交易逻辑 / 信号 / 仓位 / 架构的改动：先出计划等确认，再写代码。**
- **涉及 UI / UX 的改动：先给设计方案，review 通过后再实现。**
- 不加用户没要求的功能，不做过度抽象。
- **直接 commit 到 main**（单人项目，不走分支/PR）；commit 前跑 e2e；❌ 不能 `--no-verify` 跳 hook。
- **重启后端**：`kill -9 $(lsof -ti:8000 -sTCP:LISTEN)` 后等 ~35s LaunchAgent 自动重启。❌ 不能 `pkill -f "python main.py"`（LaunchAgent 用大写 P Python，匹配不到）。
- 改 `.py` 必须重启后端才生效（模块缓存）；改 `.tsx`/`.css` Vite HMR 自动更新。
- **Race**：`api/app.py` `_run_sp500_scan()` 的 `_scan_running=True` 守卫必须在函数**最顶部**（任何 slow import 之前）。
- **版本管理**：只有**选股/买入/卖出逻辑**变化才在 `data/versions.json` 加版本；纯 UI/参数/bugfix 不算；前端「复盘」Tab 只对比最近两版。
- **前端组件挂载**：新建 `components/Xxx.tsx` 必须在父组件 import + 渲染才生效。
