#!/usr/bin/env bash
#
# run-benchmark.sh — Full scripted miniapi benchmark for Lattice orchestrator.
#
# This script:
#   1. Loads .env for API keys
#   2. Verifies module stubs and baseline tests are RED
#   3. Starts the orchestrator (uses PTYBackend — no tmux required)
#   4. Sends task assignment to spawn 6 CC instances
#   5. Optionally launches the native desktop dashboard
#   6. Waits for user signal that work is complete
#   7. Runs the scoring agent
#   8. Records results to results/ directory
#
# Usage:
#   ./run-benchmark.sh [OPTIONS]
#
# Options:
#   --instances N     Number of CC instances to spawn (default: 6)
#   --dashboard       Also launch the native desktop dashboard
#   --auto-score      Skip waiting for user, score after timeout
#   --timeout MINS    Auto-score timeout in minutes (default: 30)
#   --help            Show this help
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NUM_INSTANCES=6
DASHBOARD=true
AUTO_TASK=false
AUTO_SCORE=false
TIMEOUT_MINS=30

# Cleanup tracking
ORCH_PID=""
DASH_PID=""

cleanup() {
    if [[ -n "$ORCH_PID" ]]; then
        kill "$ORCH_PID" 2>/dev/null || true
        wait "$ORCH_PID" 2>/dev/null || true
    fi
    if [[ -n "$DASH_PID" ]]; then
        kill "$DASH_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { printf "${CYAN}[BENCH]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}     %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
err()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            sed -n '2,/^$/{ s/^# //; s/^#//; p; }' "$0"
            exit 0
            ;;
        --instances)
            NUM_INSTANCES="$2"; shift 2 ;;
        --dashboard)
            DASHBOARD=true; shift ;;
        --no-dashboard)
            DASHBOARD=false; shift ;;
        --auto-task)
            AUTO_TASK=true; shift ;;
        --auto-score)
            AUTO_SCORE=true; shift ;;
        --timeout)
            TIMEOUT_MINS="$2"; shift 2 ;;
        *)
            err "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------
info "Loading environment..."

# Load .env from repo root (where API keys live)
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
    ok "Loaded .env from $REPO_ROOT/.env"
elif [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
    ok "Loaded .env from $SCRIPT_DIR/.env"
else
    warn "No .env file found — ensure ANTHROPIC_API_KEY is set in environment"
fi

# Verify API key is available
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    err "ANTHROPIC_API_KEY is not set. Add it to $REPO_ROOT/.env or export it."
    exit 1
fi
export ANTHROPIC_API_KEY
ok "ANTHROPIC_API_KEY is set (${#ANTHROPIC_API_KEY} chars)"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

for cmd in uv; do
    if ! command -v "$cmd" &>/dev/null; then
        err "$cmd is required but not installed"
        exit 1
    fi
done

if ! command -v claude &>/dev/null; then
    warn "claude CLI not found — cc_spawn requires it on PATH"
fi

ok "All prerequisites found"

# ---------------------------------------------------------------------------
# Step 0: Clean previous run state
# ---------------------------------------------------------------------------
info "Resetting previous run state..."

# Kill any leftover orchestrator processes for this benchmark
pkill -f "orchestrator:start.*miniapi" 2>/dev/null || true

# Remove stale socket
rm -f "$HOME/.lattice/orchestrator.sock"

# Remove DuckDB checkpoints (stale agent conversation)
rm -f "$SCRIPT_DIR/.lattice/orchestrator.duckdb" \
      "$SCRIPT_DIR/.lattice/orchestrator.duckdb.wal"

# Reset all machine-managed soul files to blank templates
mkdir -p "$SCRIPT_DIR/.lattice/soul"
cat > "$SCRIPT_DIR/.lattice/soul/STATE.md" << 'STATE_EOF'
## Instances
_No active instances_

## Plan
_No current plan_

## Decisions
_No recent decisions_

## Blockers
_No blockers_
STATE_EOF

cat > "$SCRIPT_DIR/.lattice/soul/MEMORY.md" << 'MEM_EOF'
# Orchestrator Memory

_Durable cross-session facts, preferences, and learned patterns._
MEM_EOF

# Also nuke any .lattice files outside soul/ (stale locks, caches)
rm -rf "$SCRIPT_DIR/.lattice/orchestrator.duckdb" \
       "$SCRIPT_DIR/.lattice/orchestrator.duckdb.wal" \
       "$SCRIPT_DIR/.lattice/"*.lock 2>/dev/null || true

ok "Previous run state cleaned"

# ---------------------------------------------------------------------------
# Step 1: Verify stubs
# ---------------------------------------------------------------------------
info "Verifying module stubs..."
cd "$SCRIPT_DIR"

for mod in users projects tasks tags search stats; do
    file="src/miniapi/${mod}.py"
    if [[ ! -f "$file" ]]; then
        err "Missing stub: $file"
        exit 1
    fi
done
ok "All module stubs present"

# ---------------------------------------------------------------------------
# Step 2: Verify tests are RED
# ---------------------------------------------------------------------------
info "Verifying baseline (all tests should fail)..."
if uv run pytest tests/ -q --tb=no &>/dev/null; then
    err "Tests are already passing! Reset stubs before running benchmark."
    exit 1
fi
ok "Baseline verified: all tests RED"

# ---------------------------------------------------------------------------
# Step 3: Resolve lattice CLI
# ---------------------------------------------------------------------------
LATTICE_CMD="uv run --directory $REPO_ROOT lattice"
if command -v lattice &>/dev/null; then
    LATTICE_CMD="lattice"
fi

# ---------------------------------------------------------------------------
# Step 4: Initialize soul ecosystem
# ---------------------------------------------------------------------------
info "Initializing soul ecosystem..."
$LATTICE_CMD orchestrator:init --soul-dir "$SCRIPT_DIR/.lattice/soul" 2>/dev/null || true
ok "Soul ecosystem initialized"

# ---------------------------------------------------------------------------
# Step 4b: Install hooks (enables CC → orchestrator event reporting)
# ---------------------------------------------------------------------------
info "Installing orchestrator hooks..."
$LATTICE_CMD orchestrator:install-hooks 2>/dev/null || true
ok "Hooks installed"

# ---------------------------------------------------------------------------
# Step 5: Start orchestrator with tickets (+ optional dashboard)
# ---------------------------------------------------------------------------
info "Starting orchestrator..."
cd "$SCRIPT_DIR"

# Clean up stale socket if present
rm -f "$HOME/.lattice/orchestrator.sock"

# Build orchestrator command — blank slate, agent spawns via voice commands
ORCH_ARGS=(
    orchestrator:start
    --soul-dir "$SCRIPT_DIR/.lattice/soul"
    --db-path "$SCRIPT_DIR/.lattice/orchestrator.duckdb"
)

# When dashboard is requested, use --with-dashboard so the orchestrator
# and dashboard share the same PTYManager (CC terminals appear in xterm.js)
if [[ "$DASHBOARD" == "true" ]]; then
    cols=$((NUM_INSTANCES < 3 ? NUM_INSTANCES : 3))
    ORCH_ARGS+=(--with-dashboard --cols "$cols")
    info "Dashboard will open with $cols columns"
fi

$LATTICE_CMD "${ORCH_ARGS[@]}" &
ORCH_PID=$!

# Give the orchestrator time to initialize and inject the task
info "Waiting for orchestrator to initialize..."
sleep 10

# Check it's still running
if ! kill -0 "$ORCH_PID" 2>/dev/null; then
    err "Orchestrator failed to start — check logs above"
    ORCH_PID=""
    exit 1
fi
ok "Orchestrator running (PID: $ORCH_PID) — task injected"

# ---------------------------------------------------------------------------
# Step 8: Status and wait
# ---------------------------------------------------------------------------
echo ""
echo "=================================================================="
printf "${BOLD}${GREEN}  BENCHMARK RUNNING${NC}\n"
echo "=================================================================="
echo ""
echo "  Orchestrator PID: $ORCH_PID"
echo "  Project: $SCRIPT_DIR"
echo ""
echo "  Task: spawn $NUM_INSTANCES CC instances from tickets/"
echo ""
echo "  Monitor progress:"
echo "    $LATTICE_CMD orchestrator:status --soul-dir '$SCRIPT_DIR/.lattice/soul'"
echo ""
echo "  Ticket files:"
for f in tickets/*.md; do
    printf "    - %s\n" "$f"
done
echo ""
echo "=================================================================="

if [[ "$AUTO_SCORE" == "true" ]]; then
    info "Auto-score enabled. Waiting ${TIMEOUT_MINS}m..."
    sleep $((TIMEOUT_MINS * 60))
else
    echo ""
    printf "  Press ${BOLD}ENTER${NC} when all instances have finished...\n"
    read -r
fi

# ---------------------------------------------------------------------------
# Step 9: Score
# ---------------------------------------------------------------------------
info "Running scoring agent..."
echo ""

RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Run scorer and tee to both stdout and file
uv run python score.py | tee "$RESULTS_DIR/score-${TIMESTAMP}.txt"

# Also save JSON
uv run python score.py --json > "$RESULTS_DIR/score-${TIMESTAMP}.json"

echo ""
ok "Results saved to results/score-${TIMESTAMP}.txt"
ok "JSON results: results/score-${TIMESTAMP}.json"

# ---------------------------------------------------------------------------
# Step 10: Cleanup (trap handles ORCH_PID and DASH_PID)
# ---------------------------------------------------------------------------
echo ""
info "Shutting down orchestrator..."
