# Personal Trading Agent — 系统总览（策略 + 产品路线）

> 最后更新 2026-06-02。当前策略版本 **v7**。
> 本文 = 策略总览（Part 1）+ 产品功能路线（Part 2）。权威规则/参数见 `CLAUDE.md`；下周策略待办见 memory `trading_next_week_todos.md`。

---

# Part 1 · 策略总览

## 四 Agent 流水线（单一 APScheduler，美东时间 ET）

| Agent | 职责 | 时间 ET |
|---|---|---|
| **Maya** | 读市场 regime（牛/熊/震荡）→ 定当日仓位激进度 + 板块偏好 | 8:00 |
| **Scout** | 选股：盘前 Finviz 发现 + 日内全量扫描 + AI 评分（数据源 **Alpaca 批量 bars**） | 8:45 + 9:31 / 11:00 / 12:30 / 14:30 |
| **Rex** | 执行：扫描后 cascade 买入 + 持仓监控卖出 | 买：扫描后 / 卖：≥9:31 起每 30 分钟 |
| **Vera** | 收盘复盘（手动 trigger POST /api/strategy/review） | — |

## 选股（Scout）v7 — 双轨制解耦 + 板块共振
- **Track 1 动能突破**：RSI 50–75（热板块 85）+ today_bull + mom5d>0 + vs_ma20≤15% + vol_ratio≥1.2
- **Track 2 盘整蓄力**：RSI<55 + vol_ratio<0.8 + mom5d>−3% + ma20_slope>0 + vs_ma20≥−3%
- 板块共振：同板块 ≥3 只 today_bull → 该板块 RSI 上限 75→85

## 买入（Rex）— 入场门（任一不过则跳过）
市场时间 9:25–16:05 · 信号当日 · 价格漂移≤1.5% · 财报门 ·
止损 `max(MA20×0.99, 入场−2×ATR)` 钳位 −3~−8% ·
**Gate A**（Track1 需 SPY>MA20，Track2 豁免）· **Gate B**（止损距离≤8%）·
**v7 NEUTRAL 门控**（min_ai_score 6→8、aggression 封顶 normal、size×0.75）

## 卖出（Rex）— 三层保护
1. **Alpaca bracket GTC** — 服务端主止损，毫秒级
2. **追踪止盈** — +10% 激活，高点回落 5% 触发（TRAIL 0.10/0.05）
3. **AI 软清仓** — SELL/REDUCE/HOLD，每 30 分钟评估
- v6 趋势过滤：盈利≥+5% 压制 REDUCE（让赢家跑）
- 卖出 cascade **≥9:31 才触发**（去盘前空跑）
- **买卖分级自动执行**：机械止损=0 始终执行 · AI 减仓=0.5 · 买入=0.7 → REDUCE 会自动落地

## 关键参数 & 两层止损
risk 2%/单 · 单仓上限 8% · TRAIL 10%/5% · 漂移 1.5% · 板块共振阈 3 · 自动执行阈 0.7（AI 减仓 0.5）

| 止损层 | 机制 | 阈值 | 执行方 |
|---|---|---|---|
| 主止损 | Alpaca bracket GTC | −3~−8% | Alpaca 服务端 |
| 兜底 | Holdings Monitor HARD_STOP | −8% | 每 30 分钟 refresh |

---

# Part 2 · 产品功能路线

**北极星**：系统定位是「AI 全自动执行 + 人在环监督/否决」。产品唯一目标 —— **让用户 ~10 秒看懂 AI 在做什么、并决定要不要干预。** 每个功能问一句：*它是否让"监督 AI"更快、更可信？*

| 优先级 | 功能 | 解决的问题 | 状态 |
|---|---|---|---|
| **P0-1** | K 线图 + 买卖点/止损止盈可视化 | 盲操作，无法快速否决 | ✅ 已上线 |
| **P0-2** | 决策可解释「决策卡」 | 黑箱：AI 为什么这么做 | 🟡 阶段1✅ / 阶段2待办 |
| **P1-3** | 复盘进化时间线 | 看不到系统在学习/进化 | 待办 |
| **P1-4** | 风险体检视图 | 风险散落，盘前难判断 | 待办 |
| **P2-5** | 盘前简报 / 盘后日报产品化 | 靠对话才有日报 | 待办 |
| **P2-6** | 关键事件通知（买入/止损/熔断） | 必须盯盘 | 待办 |

### P0-1 K 线图 ✅ 已上线
点持仓 symbol → 模态看日线 K 线（lightweight-charts）+ MA20/50 + 成交量 + 买入/止损/止盈/现价标注线。后端 `/api/ohlcv/{symbol}`（Alpaca）。
**后续小优化**：K 线偶尔加载 1-4s（Alpaca 延迟）→ 可加缓存提速。

### P0-2 决策卡 🟡
决策链 Maya 环境 → Scout 选股(Track/AI分/RSI/量比/理由) → Rex 执行(信号/置信度/止损止盈)，嵌持仓详情模态。
- **阶段1 ✅ 已上线**（用现有数据）
- **阶段2（待办）**：逐项门控 ✓/✗（SPY>MA20、财报、漂移、Gate A/B）→ 需给 Rex 执行路径结构化记录每个门控结果（动交易路径，先出计划）
- **扩展（待办）**：信号页（SignalsView）候选也接入 K 线 + 决策卡（数据现成，复用组件）

### P1-3 复盘进化时间线（待办）
一条可见的策略进化轨迹：每版改了什么、哪笔亏损触发、上线后胜率变化。扩展现有「复盘」Tab。数据 versions.json + trade_history 已有；"哪笔亏损触发"需补结构化关联。

### P1-4 风险体检视图（待办）
一屏风险仪表：板块集中度（半导体）、单票超 8% 预警、现金/储备、距熔断。数据 positions/budget/breaker 已有。

### P2-5 / P2-6（待办）
盘前简报/盘后日报产品化（Maya 8:00 / Vera 收盘自动生成）；关键事件通知（需新增推送通道）。

---

# Part 3 · 待办 Backlog（策略迭代 + 运营/数据）

> 本节合并原 memory `trading_next_week_todos.md`，作为唯一权威待办来源。
> 注意：产品路线（Part 2）与策略/运营 backlog（Part 3）是两条线。动手任一项前先出设计稿/计划等确认（`CLAUDE.md`「先出计划」）。

## 🔝 当前优先级（2026-06-09 重排）

今天最大发现：**实盘 PF 0.74 vs v6 回测 PF 1.33** 的落差（57 笔/胜率 35%）。主因是逆风市况 + **操作损耗**（电脑睡眠致止损滞后、额度静默耗尽）。因此把"能直接缩小落差"的运营修复提到最前：

| 顺序 | 事项 | 为什么 |
|---|---|---|
| 🥇 | **搬云 + 额度告警** | 直接消除"止损滞后 / AI 停摆"两个正在压低实盘绩效的损耗源 |
| 🥈 | **候选 A + SNPS→v8** | 信号层最高频误判 + 卖出执行链断裂，真亏损根因 |
| 🥉 | 产品 P1-4 风险体检 / P0-2 阶段2 | 提升"看得懂、敢否决"，但不直接改绩效 |

## A. 策略迭代

- 🔴 **候选 A（最高频误判源）**：高 vol_ratio 在 NEUTRAL/无趋势下降权或要额外趋势确认（区分分发 vs 积累）
  - 回归靶子：**MRVL**（RSI 71 / vol_ratio 2.21 / vs_ma20 +13.4% 仍在门内 / AI 7→HOLD），验收 `vol_ratio≥2 且 vs_ma20>10%` 时应被拦
  - 对照组（勿误伤）：AVGO/NVDA/AMAT（RSI 51–65 / vs_ma20≈0 / vol<1.1 / AI 8–9 → BUY）
  - ⚠️ 阈值需基于 **IEX volume 重新校准**（免费 feed 是 IEX-only，vol_ratio 偏低）
- 🔴 **SNPS → v8（计划已就绪）**：REDUCE 减仓「成交后」给剩余仓位补挂独立 stop 单
  - 根因：`approve_trade`（trade_agent.py:367）REDUCE 走普通市价卖单不附 stop + line 1072 守卫使有 open sell 单时 REDUCE 被跳过 → 剩余股裸奔，只靠 30 分钟 HARD_STOP_PCT=-8% 兜底
  - 方案：① 入队存 `remnant_stop_price`；② `sync_order_status` 检测 REDUCE filled 后读真实剩余股数 `place_order(stop)`，打标防重复；③ 无需改 app.py/main.py
- 🟠 **候选 B**：入场加相对强度门（vs SPY / 同行业 ETF）
- 🔧 **CAT 追高保护**：Track1 `vs_ma20≤15%` 太松（CAT -2.7%）→ `vs_ma20>10%` 时要求止损放宽或 R:R 提高（trade_agent.py Gate B 附近）
- 🟡 **中概股 / 新兴市场风险框架**（VIPS -3.3%）：低 PE 是结构性折价非机会；低 beta + 52周低点 = 有限下行是误判
- 🟡 **item 6 追踪止盈 −5% 对高波动票偏紧**：按 ATR%/波动率动态调回撤阈（高波动 7-8%，低波动 5%）（trade_agent.py TRAIL_PCT，line 966 附近）
- 🟢 **候选 C/D**：C = REDUCE 带后续退出触发（减到多少/何时全退）；D = rsi/mom/vol 全 None 时自动降级 confidence
- 📊 **item 1 Track1/2 真实胜率分析**（screen_track 埋点已上线，攒够数据后评估调参）
- 📝 **item 1b 复盘 Tab 加"实盘门控"说明**（v7 类改动机械回测体现不出，加标注）

## B. 运营 / 数据

- 🆕 🔝 **搬云**：runbook 已就绪 `docs/CLOUD_MIGRATION.md`（AWS Lightsail $7/月，us-east-1）。根治笔记本睡眠致调度静默漏跑（06-05 整天 0 运行实证）
- 🟡 **item 8 Anthropic 额度耗尽静默失败 → 告警**（06-04 踩到）：扫描 `ai_scored=0` 或 holdings AI 调用 400 时打醒目日志/推送。挂 `_run_sp500_scan` + holdings auto-refresh except 分支
- 🟡 **item 7 size_scale 跨时间超配护栏**（+10% 有界）：trade_agent.py:469 乘前，live regime ∈ {CAUTION,BEAR,NEUTRAL} 时 `size_scale_override=min(_,1.0)`
- 🟢 **item 5 清理** `src/analysis/pead_backtest.py`（未提交未引用）
- ⏳ **versions.json 版本号统一**：卖出执行类改动（买卖分级门槛 + SNPS REDUCE 补挂）下次复盘统一编号

## C. 已了结的历史复盘（保留索引）

- **2026-06-01 三笔亏损复盘**（STEP/SNPS/VIPS）：主线 = NEUTRAL 下"基本面叙事盖过价格结构" → v7 门控已硬修；高 vol_ratio 无条件看涨 = 当前最高频误判（→候选 A）
- **2026-06-01 扫描运营问题**：① yfinance 限流空扫描 ✅根治(PR#2) ② 假成功埋点 ✅(PR#4) ③ 重复触发竞争 ✅(8ef972a)

---

## ✅ 已上线基线
**2026-06-01~02**：扫描限流根治（yfinance→Alpaca 批量）· place_order 取价修复 · Scout/Rex 假成功埋点 · 重复触发竞争 · 版本归因单一事实源 · Maya 运行显示 · 盘前卖出时间门 · 买卖分级门槛 · **P0-1 K 线图** · **P0-2 决策卡阶段1** · 今日页重设计 · 生产构建修复
**2026-06-03~09**：run 记录显示 ET 时间 · UI regime 改 live（非 8:00 冻结快照）· 持仓「今日」列改 Alpaca change_today（弃 yfinance）· **trade_history 自动同步（绩效死数据根治，16:10 ET 收盘后）** · 云迁移 runbook

## 维护约定
- **策略 + 运营待办** → 本文件 Part 3（唯一权威，原 memory `trading_next_week_todos.md` 已并入）
- 动手任一项前先出设计稿/计划等确认（交易逻辑相关尤其遵守 `CLAUDE.md`「先出计划」）
- 卖出执行类改动的 **versions.json 版本号** 待下次策略复盘统一整理
