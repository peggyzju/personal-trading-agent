"""AI-edge 分析(Phase 3)— 回答"AI 评分有没有 edge"。

读 data/score_log.jsonl(Phase1 埋点 + Phase2 回填的前向收益),输出:
1. 分桶:ai_score 段 → 平均前向收益(edge = 越高分越高)
2. 每日横截面 IC(Spearman 秩相关),取时间序列均值 + t 值(处理"分数每天变/自相关")
3. 门槛分离度:≥8 vs <8 的前向收益差

去重原则:每票每日只取末次扫描(避免同日多次扫描的近重复)。
用法:python3 -m src.analysis.ai_edge_report
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

_LOG = Path(__file__).parent.parent.parent / "data" / "score_log.jsonl"
_HORIZONS = ["fwd_5d", "fwd_10d", "fwd_20d"]


def _load():
    if not _LOG.exists():
        return []
    out = []
    for line in _LOG.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _dedup_daily(rows, horizon):
    """每票每日取末次扫描(logged_at 最大),且该 horizon 已回填。"""
    best = {}
    for r in rows:
        if r.get(horizon) is None or r.get("ai_score") is None:
            continue
        key = (r.get("symbol"), r.get("scan_date"))
        if key not in best or (r.get("logged_at", "") > best[key].get("logged_at", "")):
            best[key] = r
    return list(best.values())


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0] * len(v)
        for i, idx in enumerate(order):
            rk[idx] = i
        return rk
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (sx * sy) if sx * sy else None


def report():
    rows = _load()
    print(f"score_log 总记录: {len(rows)}")
    filled = sum(1 for r in rows if any(r.get(h) is not None for h in _HORIZONS))
    print(f"已回填前向收益的: {filled}\n")
    if filled == 0:
        print("⏳ 还没有前向收益回填(候选不够老)。等 score_log 候选过 5/10/20 交易日后再跑。")
        return

    for horizon in _HORIZONS:
        data = _dedup_daily(rows, horizon)
        print(f"===== {horizon}  (去重后 {len(data)} 个候选-日) =====")
        if len(data) < 20:
            print("  样本不足(<20),先攒数据\n")
            continue

        # 1. 分桶
        buckets = defaultdict(list)
        for r in data:
            s = r["ai_score"]
            b = "≥9" if s >= 9 else "8" if s >= 8 else "7" if s >= 7 else "6" if s >= 6 else "<6"
            buckets[b].append(r[horizon])
        print("  分数 → 平均前向收益 / 胜率:")
        for b in ["≥9", "8", "7", "6", "<6"]:
            v = buckets.get(b, [])
            if v:
                print(f"    {b:>3}: n={len(v):<4} 均={statistics.mean(v):+.2f}%  胜率={sum(1 for x in v if x > 0) / len(v) * 100:.0f}%")

        # 2. 每日横截面 IC
        byday = defaultdict(list)
        for r in data:
            byday[r["scan_date"]].append(r)
        ics = [ic for d, rs in byday.items()
               if len(rs) >= 5 and (ic := _spearman([r["ai_score"] for r in rs], [r[horizon] for r in rs])) is not None]
        if ics:
            mic = statistics.mean(ics)
            t = mic * (len(ics) ** 0.5) / (statistics.stdev(ics) if len(ics) > 1 else 1)
            verdict = "正(有信号)" if mic > 0.05 else "负(反向)" if mic < -0.05 else "≈0(无关系)"
            print(f"  每日 IC: 均值={mic:+.3f}  (n={len(ics)}天, t≈{t:+.2f})  → {verdict}")
        else:
            print("  每日 IC: 横截面天数不足(每天需≥5候选)")

        # 3. 门槛分离度
        hi = [r[horizon] for r in data if r["ai_score"] >= 8]
        lo = [r[horizon] for r in data if r["ai_score"] < 8]
        if hi and lo:
            print(f"  门槛: ≥8 均={statistics.mean(hi):+.2f}%  vs  <8 均={statistics.mean(lo):+.2f}%  (差={statistics.mean(hi) - statistics.mean(lo):+.2f}%)")
        print()

    print("判读:分桶单调↑ + IC>0.05 显著 + ≥8差为正 → AI 有 edge;平坦/IC≈0 → 讲故事,选股要重做。")


if __name__ == "__main__":
    report()
