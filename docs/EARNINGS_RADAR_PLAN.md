# 财报雷达 Earnings Radar — 设计文档(已确认 2026-06-25)

> 事件驱动子系统:不依赖未验证的选股 edge。利用"财报"这个短线最强催化剂 ——
> 提前给出全市场财报日历(重点盯持仓),财报公布后实时 AI 研判,人工决策。
> 背景:现系统只"回避"财报(财报前不买);且选股不可靠,会错过重要财报(如 MU 美光)。

## 已确认的产品决策
- **范围**:全市场(`data/sp500_constituents.txt` 487 只),持仓优先 —— 不漏重要财报
- **数据源**:**Finnhub**(2026-06-25 从 yfinance 切换 —— 实测 yfinance 滞后一年多、拿不到 2026 财报)。
  财报日历(批量1次调用,带 BMO/AMC + EPS预期)+ 历史 EPS 超预期(含本年)用 Finnhub;
  价格反应用 Alpaca(`feed="iex"`)。key 在 `.env: FINNHUB_API_KEY`,免费版 60次/分钟。
  历史财报"反应日"= 财季窗口内成交量最大那天(Finnhub 免费版历史只给公布日不给逐次日期)
- **通知**:桌面通知(osascript);**不发邮件**(用户不看)
- **自动化**:仅"提醒 + AI 研判",**人工决策,系统不自动下单**(MVP 不接 Rex)
- **dashboard 位置**:`Agent 运行` 面板下方、`收益` 面板上方

## Part A — 财报日历(挂进 Maya,每天 8:00 ET 全量生成)
- 在 `market_context` 流程里追加:抓未来 7 天全市场财报日历 → 写 `data/earnings_calendar.json`
- 每行字段:`symbol, company, date, session(BMO盘前/AMC盘后), eps_estimate, in_portfolio, importance`
- 排序/标记:**持仓发财报 → 置顶 + 标红 + 顶部警示条(裸穿跳空风险)**;其余按距今天数排;watchlist/龙头标"关注"
- yfinance 全量 487 只逐个查较慢 → 带每日缓存,失败的 symbol 跳过不阻塞

## Part B — 财报后实时 AI 研判
- **触发**:从日历取"当日发财报"的小名单 → 每 ~10-15 分钟轮询其价格反应(盘后/盘前跳空% + 量比),检测到显著反应**立即**生成研判 + 桌面通知
- **AI 输入**:EPS 实际 vs 预期(beat/miss%)、营收、跳空%、量比、近期新闻标题(复用 news_monitor/sentiment)、技术位、**该票历史财报后表现(过去几次财报后涨跌)**
- **AI 输出(两种模式,自动判断是否持有)**:
  - 未持有 → 「入场研判」:值得关注 / 观望(含追高提示)
  - 已持有 → 「持仓建议」:继续持有 / 减仓 / 清仓
  - + 信心分 + 理由摘要
- **真实性局限(诚实)**:yfinance 拿不到实时 EPS 数字 → 触发主要靠**价格反应**(市场的裁决,对交易反而更重要);拿不到管理层指引全文 → 研判基于 beat + 反应 + 新闻标题,非完整电话会分析

## UI(已出视觉稿,确认)
- **财报日历面板**:见 mockup;持仓标红置顶 + 警示条
- **财报后研判卡片**:跳空% 大字 + EPS/营收/量比三格 + AI 摘要 + 研判结论(入场/持仓)+ **历史财报后表现** + 按钮「查看K线 / 标记已读」+ "人工决策不自动下单"标注

## 复用现有
- `news_monitor.get_earnings_calendar / earnings_within_days`(yfinance 财报日期)
- AI 调用:`get_anthropic_client()`(已加超时)
- 桌面通知:osascript(看门狗已用)
- 调度韧性:心跳 + 看门狗(已上线)兜底新加的定时任务

## 实现顺序(MVP)
1. **`src/monitor/earnings_radar.py`**:① `build_calendar()` 全量抓+排序+缓存;② `detect_reactions()` 当日名单价格反应检测;③ `analyze_earnings(symbol)` AI 研判(含历史财报后表现)
2. **接 Maya**:`main.py run_market_context` 末尾调 `build_calendar()`;新增调度 `detect_reactions`(每 10-15 分钟,市场+盘后/盘前时段)
3. **API**:`GET /api/earnings/calendar`、`GET /api/earnings/analysis/{symbol}`、研判结果缓存 `data/earnings_analysis.json`
4. **前端**:`EarningsCalendar.tsx` + `EarningsAlertCard.tsx`,挂到 dashboard(Agent运行 与 收益 之间)
5. **测试**:e2e 加断言(日历生成、调度注册);commit

## 不在 MVP 范围(以后)
- 自动下单 / 接 Rex 作为买入来源(阶段2,验证研判有效后)
- 付费实时 EPS 源
- 邮件/手机推送
