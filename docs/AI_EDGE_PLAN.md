# AI 层 Edge 测量计划（埋点 + 前向收益分析）

> 目标：回答"**AI 评分到底有没有 edge**" —— 即 ai_score 能否 rank-order 候选的未来涨跌。
> 关键优势：**不需要成交、不需要干净执行、不需要牛市** —— 只要"打了分的候选 + 它们之后的价格"。所以**现在就能开始攒数据,不被搬云/熊市卡**。
> 性质：纯埋点 + 分析,**不碰任何交易逻辑**。

---

## 核心命题
AI 有 edge ⟺ 它打高分的候选,之后**确实**比低分的涨得多(单调 + 正相关)。
用标准量化做法验证:**分桶平均前向收益 + IC(信息系数,秩相关)+ 门槛分离度**。

---

## Phase 1 — 评分埋点(逐候选,append-only)

**落点**：`api/app.py` `_run_sp500_scan`,`top_ai` 算出后(line ~512),加一行 `record_scored_candidates(top_ai, regime=ctx["regime"], min_ai_score=..., scanned_at=...)`。包 try/except,**绝不影响扫描主流程**。

**存储**：`data/score_log.jsonl`(每行一个 JSON,每次扫描 append ~15 行;append-only,不滚动)。

**每行 schema**：
```json
{
  "logged_at": "2026-06-09T13:31:00Z",
  "scan_date": "2026-06-09",        // ET 交易日
  "symbol": "NVDA",
  "ai_score": 7,
  "signal": "BUY",                  // STRONG_BUY/BUY/HOLD/SELL
  "regime": "CAUTION",
  "min_ai_score": 8,                // 当时买入门槛(用于门槛检验)
  "screen_track": "momentum",
  "price": 213.5, "rsi": 52.4, "momentum_5d": -2.1, "momentum_1m": 3.4,
  "vol_ratio": 1.1, "vs_ma20": 0.5, "sector": "SEMIS", "today_bull": true,
  "fwd_5d": null, "fwd_10d": null, "fwd_20d": null,  // Phase 2 回填
  "fwd_filled_at": null
}
```
> 同一票一天多次扫描 = 多行(都存);分析时可去重到"每票每日首次"。

## Phase 2 — 前向收益回填(批量,定时 + 可手动)

**新定时任务**：每交易日 **16:20 ET**(收盘后、trade_history 同步之后)跑 `fill_forward_returns()`：
- 读 `score_log.jsonl`,对 `fwd_Nd is null 且 scan_date + N 个交易日 ≤ 今天` 的行;
- 用 Alpaca 日线(`fetch_bars_batch`,按票批量)取 scan_date 收盘 → scan_date+N 收盘;
- `fwd_Nd = (close[+N] - close[0]) / close[0] * 100`,回写。
- 只用**打分日之后**的价格 → 无 look-ahead。
- 取不到价(退市/缺数据)→ 跳过、标记。

水平：**5 / 10 / 20 交易日**三档(短中长)。

## Phase 3 — 分析(on-demand 脚本 / 后续可做 UI 面板)

读回填后的 `score_log.jsonl`,输出:

1. **分桶表**(主):ai_score 分桶(`<6 / 6 / 7 / 8 / 9-10`)→ 平均&中位 fwd_10d、n、胜率(fwd>0)
   ```
   score≥9 → 平均 +X%  n=..
   score=8 → +Y%
   score=7 → +Z%
   ...
   edge = 越高分,平均前向收益越高(单调)
   ```
2. **IC**:Spearman 秩相关(ai_score, fwd) per horizon。
   - IC>0.05 算有信号;**需 n≥~200 才稳**;显著性 t ≈ IC×√n。
3. **门槛分离度**:`ai_score≥8` vs `<8` 的前向收益差 —— 验证"只买≥8"这条门槛是不是真把好票分出来了。
4. **多空 spread**:最高桶 − 最低桶 = edge 的量级。
5. **控制变量**:按 regime / sector 切分,看 edge 在子样本里是否还在(防"只是 regime/板块效应")。

## 并行 — AI 软清仓 edge(验证问题 B 的"砍赢家")

同法埋点**每次 holdings AI 卖出信号**(SELL/REDUCE + 当时盈利 + 日期)→ 回填信号后前向收益:
- 信号后**继续跌** = 判对(该卖);**继续涨** = 砍了赢家(问题 B 实锤)。
- 落点:`holdings_monitor` 的 `analyze_sell_signals` 出口,append 到 `data/sell_signal_log.jsonl`。

---

## 样本与时间预期
- 每次扫描 ~15 打分,4 次/日 → 去重后 ~10-20 票/日 → ~200-400/月。
- **首读 ~3-4 周**(够分桶看趋势);**有信心结论 ~2-3 月**(IC 稳)。
- **熊市照样扫描照样打分 → 数据照样攒**(不被 regime 卡,这是关键)。

## 诚实的 caveat
- **前向收益 ≠ 可交易 P&L**(无止损/滑点)—— 它测的是**信号质量(edge)**,正是我们要的;实盘 P&L 另算。
- 测的是 AI 在**机械筛选池之上**的边际区分力(正确的问题)。
- IC 小样本噪音大,**早期别过度解读**。
- 去重/缺价/退市要处理。

## 分期与风险
- **Phase 1 埋点**:小、加法、无交易逻辑 → **先做,立即开始攒数据**。
- **Phase 2 回填**:定时任务 + 手动脚本。
- **Phase 3 分析**:脚本,攒够样本后跑。
- 全程**不动交易逻辑**,埋点是 append 副作用(try/except 包好,绝不影响扫描/交易)。

---

## 这件事的意义
这是**唯一能真正回答"AI 层有没有 edge"、且现在就能启动、不被搬云/熊市卡**的工作。
- 若分桶单调 + IC 显著为正 → AI 有 edge,问题回到"执行/退出怎么兑现它"。
- 若分桶平坦 + IC≈0 → **AI 没 edge,讲故事而已** → 整个策略要重新想(选股逻辑、是否换 AI 用法)。

无论哪个结论,都比现在"不知道"强 —— 它把最根本的未知变成可测。
