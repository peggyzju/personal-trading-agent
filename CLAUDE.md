# Personal Trading Agent — Claude Code 指南

> 当前策略版本 **v8（趋势统一，2026-06-29）**。历史版本见 `data/versions.json`。
> 产品路线 / 策略 backlog 见 `docs/BACKLOG.md`（不自动加载，需要时才看）。

---

## 1. 目标

用 AI 驱动的自动化系统在美股做**短线波段**（持仓 5–10 天），运行在 **paper trading**（Alpaca）。
定位：**AI 全自动执行 + 人在环监督/否决**。

- **选股**：机械动量，只买“上升趋势中的强势股”
- **买入**：信号驱动 + 严格入场门 + 默认自动执行（AI 排雷 → 转人工）
- **卖出**：v8 纯机械退出（−8% 止损 + 追踪止盈 + MA20 破位；**无 AI 卖出**）
- **复盘**：收盘 AI 分析胜负，提取教训注入下一轮

---

## 2. Agent 执行机制 + 每个 Agent 策略

### 执行机制
- 单一 APScheduler 调度，四 Agent 协同，全程**美东时间 ET**。
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

### Scout — 选股（纯机械动量，无 AI 选股）
- **数据**：~169 只 universe（S&P500 + 纳指100 + Layer2）的 **Alpaca 批量日线 bars** → 算 MA50/MA20/RSI/momentum_3m/vs_ma20
- **时间**：8:45 盘前发现（Finviz）+ 9:31 / 11:00 / 12:30 / 14:30 扫描
- **策略**：**趋势门 4 硬条件 → 按 `momentum_3m` 排名**
  - 价 > MA50 且 MA50 上升（5日斜率>0）· RSI 50–80 · 3月动量>0 · vs_ma20 ≤ 15%
  - 过门后按 `momentum_3m`（63 交易日涨幅%）降序取 top N；自选（force_symbols）**走同一道门、不特殊、无买入优先权**
  - **动量窗口 = 3mo 的决策**：稳健性 9/9 赢 SPY；120d 纯回测更高但仍选 3mo（匹配 5–10 天持仓 + 防幸存者偏差）。**要换先做去幸存者偏差重测**
  - AI **不选股**（仅用于排雷 / 财报 / 复盘）

### Rex — 买入（v8 纯机械）
- **数据**：Scout 候选（含扫描价）· Alpaca 实时价（漂移/执行）· Finnhub 财报 · regime · 熔断状态 · 当前持仓 + pending
- **时间**：每次扫描后 cascade
- **策略**：候选 = 扫描 top-N ∪ 自选（同门）→ 逐个过入场门 → 自动执行
  - **槽位上限**：`max(0, regime.max_positions − 持仓 − 已pending买单)`，买入循环内**逐个递减、满即 break**（`_slots_remaining()`）。pending 占位，防两次扫描叠加超额（6-30 暴冲根因，勿回退）
  - 市场时间 9:30–16:00 ET · 扫描信号非当日跳过 · 财报今/明未公布跳过（已公布放行）
  - **价格漂移门**（`_price_drift_decision`）：现价比扫描价**涨 >1.5% 拒单**（追高失效）；跌/阈内放行（更好入场）
  - 止损固定 −8%（`round(price×0.92,2)`，容差 8.5%）· regime 门（BEAR 全停）· **熔断**（单日组合 ≤−5% 当天停买）· **WSB extreme** 仓位砍半
  - 仓位：risk 2%/单，单仓 ≤8%，现金留 ≥5%，按 `size_factor` 缩放
  - **自动/人工**：默认自动（confidence 固定 0.8；自动审批**纯开关**无阈值）；`veto=True` → 人工队列，**2h 未处理自动作废、释放槽位补位**（`expires_at=created+2h`）
  - **下单**：Alpaca bracket（市价入场 + −8% 止损），时效 **GTC**

### Rex — 卖出（v8 纯机械退出，无 AI，每 30 分钟）
- **数据**：持仓（Alpaca）· 实时价 · 日线（算 MA20/RSI）· `trailing_stops.json` 高水位
- **时间**：≥9:31 起每 30 分钟
- **策略**：`_rule_based_override` 优先级 **硬止损 > 追踪止盈 > MA20 破位**
  1. **硬止损**：plpc ≤ −8%
  2. **追踪止盈**：高水位 +6% 激活 → 从高点回撤 8% 触发（`TRAIL_ACTIVATE_PCT=6.0 / TRAIL_PCT=8.0`）
  3. **MA20 破位**：**连续 2 根**日线收盘 < MA20（`ma20_below_2d`，过滤单根假破位/健康回调）
  - 主动卖出前先**取消保护止损单**再 `close_position`
  - **超配保护（非策略性卖出）**：持仓市值 > 权益 95% → 卖最差盈亏直到降回 90%（防杠杆）

### Vera — 收盘复盘
- **数据**：`trade_history` · `versions.json`
- **时间**：手动 trigger（`POST /api/strategy/review`）
- **策略**：分析胜负特征 → 定性 notes 注入下一轮 AI context（v8 里 AI 不买卖，只影响排雷/财报语境）

### 关键参数（勿随意改）
`MIN_CASH_PCT=0.05` · `risk_pct=0.02` · `max_pos_pct=0.08` · 止损固定 8% · `TRAIL_ACTIVATE_PCT=6.0`/`TRAIL_PCT=8.0`(holdings_monitor) · `PRICE_DRIFT_THRESHOLD=0.015` · veto TTL 2h · `SECTOR_RESONANCE_THRESHOLD=3`

---

## 3. 测试

- 任何功能改动 commit **之前必须跑测试并汇报**，只有 ✅ 才 commit，❌ 先修。
- 改**买卖逻辑**必须先让下面「v8 机械规则」测试全绿。

```bash
python3 tests/e2e_daily.py --smoke   # smoke（必跑，~30s）
python3 tests/e2e_daily.py           # full（~2min）
```

**覆盖**：环境 / 账户 / Market Regime / Scanner / Strategy Notes / 自主模式（纯开关）/ Vera 复盘 / 数据契约 / 调度器 · **v8 机械规则（smoke 也跑，守实盘根因）**：
- `test_slot_cap` 槽位上限（防超额，6-30 暴冲）· `test_bracket_gtc` bracket=GTC（防裸奔）
- `test_ma20_exit` MA20 连续2根破位 · `test_price_drift_gate` 漂移门（涨拒跌放）· `test_veto_ttl` veto 2h
- `test_hard_stop_logic` 硬止损 + 追踪止盈（hard stop 场景须用 ≤−8%，如 −9%）

---

## 4. Coding 规则

- 回复用**中文**；一律用**美东时间 ET**对话。
- **交易逻辑 / 信号 / 仓位 / 架构的改动：先出计划等确认，再写代码。** 尤其：**AI-edge 未验证前别调卖出/参数/仓位** —— 三次退出类回测（收紧止损 / stale 死钱 / 放宽追踪）已全否决，退出机制近最优；系统赚不赚钱压在“AI 选股有没有 edge”（周六自动 edge 报告，详见 `docs/BACKLOG.md`）。
- 不加用户没要求的功能，不做过度抽象。
- **直接 commit 到 main**（单人项目，不走分支/PR）；commit 前跑 e2e；❌ 不能 `--no-verify` 跳 hook。
- **重启后端**：`kill -9 $(lsof -ti:8000 -sTCP:LISTEN)` 后等 ~35s LaunchAgent 自动重启。❌ 不能 `pkill -f "python main.py"`（LaunchAgent 用大写 P Python，匹配不到）。
- 改 `.py` 必须重启后端才生效（模块缓存）；改 `.tsx`/`.css` Vite HMR 自动更新。
- **Race**：`api/app.py` `_run_sp500_scan()` 的 `_scan_running=True` 守卫必须在函数**最顶部**（任何 slow import 之前）。
- **版本管理**：只有**选股/买入/卖出逻辑**变化才在 `data/versions.json` 加版本；纯 UI/参数/bugfix 不算；前端「复盘」Tab 只对比最近两版。
- **前端组件挂载**：新建 `components/Xxx.tsx` 必须在父组件 import + 渲染才生效。
