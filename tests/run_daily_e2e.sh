#!/bin/bash
# run_daily_e2e.sh — Pre-market E2E test runner
# Triggered by LaunchAgent at 4:30 AM CT (= 5:30 AM ET) on weekdays
# Full run if core files changed since last test; smoke-only otherwise.

set -euo pipefail

PROJECT_DIR="/Users/tingaling97/personal-trading-agent"
LOG_FILE="$PROJECT_DIR/data/e2e_report.log"
LAST_COMMIT_FILE="$PROJECT_DIR/data/.last_e2e_commit"
PYTHON="/usr/bin/python3"

CORE_FILES=(
    "src/trader/trade_agent.py"
    "src/analysis/strategy_reviewer.py"
    "src/analysis/stock_screener.py"
    "src/analysis/ai_analyst.py"
    "src/monitor/holdings_monitor.py"
    "api/app.py"
)

mkdir -p "$PROJECT_DIR/data"

# ── Weekday check ─────────────────────────────────────────────────────────────
DOW=$(date +%u)   # 1=Mon … 7=Sun
if [ "$DOW" -gt 5 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M')] Weekend — skipping E2E" >> "$LOG_FILE"
    exit 0
fi

cd "$PROJECT_DIR"

# ── Detect core file changes since last test ──────────────────────────────────
CURRENT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "no-git")
LAST_COMMIT=""
[ -f "$LAST_COMMIT_FILE" ] && LAST_COMMIT=$(cat "$LAST_COMMIT_FILE")

CHANGED=false
CHANGED_LIST=""

if [ "$CURRENT_COMMIT" != "no-git" ] && [ -n "$LAST_COMMIT" ] && [ "$CURRENT_COMMIT" != "$LAST_COMMIT" ]; then
    for f in "${CORE_FILES[@]}"; do
        if git diff --name-only "$LAST_COMMIT" "$CURRENT_COMMIT" 2>/dev/null | grep -qF "$f"; then
            CHANGED=true
            CHANGED_LIST="$CHANGED_LIST\n  • $f"
        fi
    done
else
    # First run or no git — always full
    CHANGED=true
fi

# ── Determine test scope ──────────────────────────────────────────────────────
if $CHANGED; then
    TEST_MODE="full"
    TEST_ARGS=""
else
    TEST_MODE="smoke"
    TEST_ARGS="--smoke"
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
{
    echo ""
    echo "══════════════════════════════════════════════════"
    echo "[$TIMESTAMP] E2E — $TEST_MODE mode"
    if [ -n "$CHANGED_LIST" ]; then
        printf "Changed files:$CHANGED_LIST\n"
    fi
    echo "══════════════════════════════════════════════════"
} >> "$LOG_FILE"

# ── Run tests ─────────────────────────────────────────────────────────────────
set +e
OUTPUT=$("$PYTHON" "$PROJECT_DIR/tests/e2e_daily.py" $TEST_ARGS 2>&1)
EXIT_CODE=$?
set -e

echo "$OUTPUT" >> "$LOG_FILE"

# ── Parse results for notification ───────────────────────────────────────────
PASSED=$(echo "$OUTPUT" | grep -oE '通过: [0-9]+' | grep -oE '[0-9]+' | tail -1 || echo "?")
FAILED=$(echo "$OUTPUT" | grep -oE '失败: [0-9]+' | grep -oE '[0-9]+' | tail -1 || echo "0")

if [ "$EXIT_CODE" -eq 0 ]; then
    TITLE="✅ 开盘前测试通过"
    MSG="${PASSED} 项通过 [$TEST_MODE] — 系统就绪"
else
    TITLE="❌ 开盘前测试失败"
    MSG="${FAILED} 项失败 [$TEST_MODE] — 开盘前请检查 data/e2e_report.log"
fi

osascript -e "display notification \"$MSG\" with title \"$TITLE\" sound name \"Glass\"" 2>/dev/null || true

# ── Save commit for next diff ─────────────────────────────────────────────────
echo "$CURRENT_COMMIT" > "$LAST_COMMIT_FILE"

echo "[$TIMESTAMP] Done: exit=$EXIT_CODE passed=$PASSED failed=$FAILED" >> "$LOG_FILE"

exit $EXIT_CODE
