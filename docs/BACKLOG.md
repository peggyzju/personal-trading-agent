# Backlog — 产品路线 + 运营 + 策略结论

> 非自动加载,需要时才看。权威操作规则/策略见 `CLAUDE.md`。
> 已删除的 v7 时代 backlog(Track1/2 胜率、Gate B 追高、REDUCE 补挂、候选 A/C/D、高 vol_ratio 降权、2026-06-09 诊断优先级、已上线基线清单等)—— 对应机制 v8 已移除,失效;历史见 git log。

---

## 🧭 核心结论(策略护栏)

- **退出机制近最优,别动**:三次退出/止损类回测全被否决 —— 收紧止损(稳健有害/whipsaw)· stale 死钱止损(不稳健 + 测不了轮动)· 放宽追踪(年份依赖)。**杠杆在买(edge)不在卖。**
- **唯一指向核心的工作 = AI-edge 验证**:实盘机械层无稳定 edge → 系统赚不赚钱压在“AI 选股有没有 edge”。
  - 每周六自动跑 AI-edge 分析并提醒结论(scheduled task `ai-edge-weekly-check`;三阶段埋点:`score_log.jsonl` / 前向收益回填 / `ai_edge_report` 分桶+IC)。
  - 判定:分桶单调↑ 且 IC>0 = 有 edge(修执行);平坦/IC≈0 = 没 edge(选股重做)。
- **edge 验证前,任何卖出/参数/仓位微调都是徒劳**(已实证)。

---

## 📦 产品功能路线(先不放进 CLAUDE.md)

北极星:让用户 ~10 秒看懂 AI 在做什么、并决定要不要干预。

| 优先级 | 功能 | 状态 |
|---|---|---|
| P0-1 | K 线图 + 买卖点/止损可视化 | ✅ 已上线(+ K线分析弹窗、主从布局) |
| P0-2 | 决策可解释「决策卡」 | 🟡 阶段1✅;信号页已接 K线+门控面板 |
| P1-3 | 复盘进化时间线(每版改了什么 / 哪笔亏损触发 / 胜率变化) | 待办 |
| P1-4 | 风险体检视图(板块集中度 / 单票超8%预警 / 现金 / 距熔断) | 待办 |
| P2-5 | 盘前简报 / 盘后日报产品化(Maya 8:00 / Vera 收盘自动生成) | 待办 |
| P2-6 | 关键事件通知(买入/止损/熔断,需新增推送通道) | 待办 |

---

## ⚙️ 运营 / 数据

- ✅ **调度器韧性加固**(2026-06-25, commit 0542eb6):`socket.setdefaulttimeout(90)` + `get_anthropic_client(timeout=60)` 防冻结 · `job_defaults max_instances=1+coalesce` · 心跳 `data/scheduler_heartbeat.json` + 独立看门狗 LaunchAgent `com.trading-agent.watchdog`(市场时段心跳>35min 未更新→重启+告警,15min 冷却)。
- 🟡 **搬云**(可选):runbook `docs/CLOUD_MIGRATION.md`(AWS Lightsail $7/月)。定位=解决“睡眠漏跑”;冻结已被看门狗覆盖,优先级下调。
- 🟡 **额度耗尽静默失败 → 告警**:扫描 `ai_scored=0` 或 holdings AI 调用 400 时打醒目日志/推送。
- 🟡 **资金利用率 ~60%**(提高仓位——**必须等 edge 验证**):BULL 满 10 仓仅部署约 62%(两条上限叠加:max_positions×每仓 risk/止损/8%封顶)。亏钱策略上加仓=放大亏损,edge 证明为正再谈提高 max_positions 或每仓。
