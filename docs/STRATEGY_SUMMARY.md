# 交易系统 — 策略总结

> 最后更新 2026-06-02。当前策略版本 **v7**。这是"当前实际运行"的总览；权威细节见 `CLAUDE.md`，下周待办见 memory `trading_next_week_todos.md`，产品 roadmap 见 `docs/PRODUCT_ROADMAP.md`。

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
市场时间 9:25–16:05 · 信号当日 · 价格漂移≤1.5% · 财报门（今/明未公布跳过）·
止损 `max(MA20×0.99, 入场−2×ATR)` 钳位 −3~−8% ·
**Gate A**（Track1 需 SPY>MA20，Track2 豁免）· **Gate B**（止损距离≤8%）·
**v7 NEUTRAL 门控**（min_ai_score 6→8、aggression 封顶 normal、size×0.75）

## 卖出（Rex）— 三层保护
1. **Alpaca bracket GTC** — 服务端主止损，入场时挂，毫秒级
2. **追踪止盈** — +10% 激活，高点回落 5% 触发（TRAIL_TRIGGER 0.10 / TRAIL_PCT 0.05）
3. **AI 软清仓** — SELL/REDUCE/HOLD，每 30 分钟评估
- v6 趋势过滤：持仓盈利≥+5% 压制 REDUCE（让赢家跑）
- 卖出 cascade **≥9:31 才触发**（去盘前空跑，2026-06-02）
- **买卖分级自动执行门槛**（2026-06-02）：机械止损 hard/trail_stop=0 始终执行 · AI 减仓 holdings=0.5 · 买入=0.7 → REDUCE 现在会自动落地

## 关键参数（勿随意改，见 CLAUDE.md 表）
risk 2%/单 · 单仓上限 8% · TRAIL 10%/5% · 价格漂移 1.5% · 板块共振阈 3 · 自动执行阈 0.7（卖出分级后 AI 减仓 0.5）

## 风控两层止损（勿混淆）
| 层 | 机制 | 阈值 | 执行方 |
|---|---|---|---|
| 主止损 | Alpaca bracket GTC | 入场算，−3~−8% | Alpaca 服务端 |
| 兜底 | Holdings Monitor HARD_STOP | −8% | 每 30 分钟 refresh |

---

## ✅ 2026-06-01~02 已上线（基础设施 / 执行 / UI，全在 main）
- 扫描限流根治（yfinance → Alpaca 批量 bars）
- place_order 取价改 Alpaca + 修退化缺参数 bug
- Scout/Rex 假成功埋点修复（skip/故障空/吞异常不再误记 success）
- 重复触发竞争修复（Rex 不自起后台扫描，定时扫描不被顶）
- 版本归因合并到单一事实源（versions.json）
- Maya 运行显示取调度记录（不再被 context 文件污染）
- 盘前卖出时间门（卖出 cascade ≥9:31）
- 买卖分级自动执行门槛（保护性卖出更易放行）
- **K 线图 + 决策卡**（点持仓 symbol → 蜡烛/MA/量/止损止盈线 + Maya→Scout→Rex 决策链）
- 今日页重设计（持仓主表格 + 侧栏审批/交易记录）+ 生产构建修复

## 待办与版本号
- **下周待办**（信号层候选 A/B、SNPS v8、中概股框架、Track 胜率分析、IEX volume 观察等）：见 memory `trading_next_week_todos.md`
- **版本号**：卖出分级门槛 + SNPS REDUCE 补挂止损 等卖出执行改动，待下次策略复盘统一整理进 `versions.json`
