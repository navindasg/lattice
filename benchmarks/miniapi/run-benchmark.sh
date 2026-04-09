#!/usr/bin/env bash
#
# run-benchmark.sh — Full scripted miniapi benchmark for Lattice orchestrator.
#
# This script:
#   1. Resets the miniapi project to stub state (git checkout)
#   2. Verifies all tests are RED (baseline)
#   3. Starts tmux session with N Claude Code instances
#   4. Initializes the Lattice orchestrator
#   5. Launches the TUI dashboard (optional, in a split pane)
#   6. Prints the voice command to speak
#   7. Waits for user signal that work is complete
#   8. Runs the scoring agent
#   9. Records results to results/ directory
#
# Usage:
#   ./run-benchmark.sh [OPTIONS]
#
# Options:
#   --instances N     Number of CC instances to spawn (default: 6)
#   --dashboard       Also launch the TUI dashboard
#   --auto-score      Skip waiting for user, score after timeout
#   --timeout MINS    Auto-score timeout in minutes (default: 30)
#   --help            Show this help
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION_NAME="miniapi-bench"
NUM_INSTANCES=6
DASHBOARD=false
AUTO_SCORE=false
TIMEOUT_MINS=30

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
        --auto-score)
            AUTO_SCORE=true; shift ;;
        --timeout)
            TIMEOUT_MINS="$2"; shift 2 ;;
        *)
            err "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

for cmd in tmux uv; do
    if ! command -v "$cmd" &>/dev/null; then
        err "$cmd is required but not installed"
        exit 1
    fi
done

if ! command -v claude &>/dev/null; then
    warn "claude CLI not found — orchestrator cc_spawn will need it on PATH"
fi

ok "All prerequisites found"

# ---------------------------------------------------------------------------
# Step 1: Reset to stub state
# ---------------------------------------------------------------------------
info "Resetting miniapi to stub state..."
cd "$SCRIPT_DIR"

# Verify stubs are in place (router-only, no endpoints)
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
if uv run pytest tests/ -q --tb=no 2>/dev/null; then
    err "Tests are already passing! Reset stubs before running benchmark."
    exit 1
fi
ok "Baseline verified: all tests RED"

# ---------------------------------------------------------------------------
# Step 3: Resolve lattice CLI
# ---------------------------------------------------------------------------
LATTICE_PKG="$(cd "$SCRIPT_DIR/../.." && pwd)"
if command -v lattice &>/dev/null; then
    LATTICE_CMD="lattice"
else
    LATTICE_CMD="uv run --directory $LATTICE_PKG lattice"
fi

# ---------------------------------------------------------------------------
# Step 4: Create tmux session and start orchestrator
# ---------------------------------------------------------------------------
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    warn "Session '$SESSION_NAME' exists, killing it"
    tmux kill-session -t "$SESSION_NAME"
fi

info "Creating tmux session: $SESSION_NAME"
tmux new-session -d -s "$SESSION_NAME" -c "$SCRIPT_DIR"

# Initialize soul ecosystem
info "Initializing Lattice orchestrator..."
$LATTICE_CMD orchestrator:init --soul-dir "$SCRIPT_DIR/.lattice/soul" 2>/dev/null || true
ok "Soul ecosystem initialized"

# Start the orchestrator with voice in the tmux session.
# The orchestrator's cc_spawn tool will create CC instances on demand
# when instructed via voice command.
info "Starting orchestrator in tmux session..."
ORCH_CMD="$LATTICE_CMD orchestrator:voice --soul-dir '$SCRIPT_DIR/.lattice/soul' --project '$SCRIPT_DIR'"
tmux send-keys -t "$SESSION_NAME" "$ORCH_CMD" Enter
ok "Orchestrator starting (voice mode)"

# Give the orchestrator a moment to initialize
info "Waiting for orchestrator to initialize..."
sleep 5

# ---------------------------------------------------------------------------
# Step 5: Dashboard (optional)
# ---------------------------------------------------------------------------
if [[ "$DASHBOARD" == "true" ]]; then
    info "Launching native desktop dashboard..."
    cols=$((NUM_INSTANCES < 3 ? NUM_INSTANCES : 3))
    # Launch the native pywebview dashboard as a detached background process.
    # - >/dev/null 2>&1 suppresses debug polling logs from mixing with output
    # - disown removes the process from bash's job table so Ctrl+C and script
    #   exit don't send signals to it (prevents the GIL/Cocoa crash)
    # - The dashboard window appears as a native macOS window on screen
    $LATTICE_CMD ui:dashboard --cols "$cols" --soul-dir "$SCRIPT_DIR/.lattice/soul" >/dev/null 2>&1 &
    DASH_PID=$!
    disown "$DASH_PID"
    ok "Native dashboard window opened (PID: $DASH_PID)"
fi

# ---------------------------------------------------------------------------
# Step 6: Print voice command
# ---------------------------------------------------------------------------
echo ""
echo "=================================================================="
printf "${BOLD}${GREEN}  BENCHMARK READY${NC}\n"
echo "=================================================================="
echo ""
echo "  Orchestrator is running in tmux with voice enabled."
echo "  Attach with: tmux attach -t $SESSION_NAME"
echo ""
printf "${BOLD}  Say this to the orchestrator:${NC}\n"
echo ""
printf "  ${CYAN}\"There are 6 tickets in the tickets/ directory.${NC}\n"
printf "  ${CYAN} Spawn 6 Claude Code instances and assign one ticket${NC}\n"
printf "  ${CYAN} to each. Each instance should read its ticket and${NC}\n"
printf "  ${CYAN} implement the module. The project directory is $(pwd).\"${NC}\n"
echo ""
echo "  The orchestrator will use cc_spawn to create each instance"
echo "  and send the ticket assignment as the first prompt."
echo ""
echo "  Ticket files:"
for f in tickets/*.md; do
    printf "    - %s\n" "$f"
done
echo ""
echo "=================================================================="

# ---------------------------------------------------------------------------
# Step 7: Wait for completion
# ---------------------------------------------------------------------------
if [[ "$AUTO_SCORE" == "true" ]]; then
    info "Auto-score enabled. Waiting ${TIMEOUT_MINS}m..."
    sleep $((TIMEOUT_MINS * 60))
else
    echo ""
    printf "  Press ${BOLD}ENTER${NC} when all instances have finished...\n"
    read -r
fi

# ---------------------------------------------------------------------------
# Step 8: Score
# ---------------------------------------------------------------------------
info "Running scoring agent..."
echo ""

# Create results directory
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
# Step 9: Cleanup
# ---------------------------------------------------------------------------
if [[ "${DASH_PID:-}" ]]; then
    kill "$DASH_PID" 2>/dev/null || true
fi

echo ""
printf "  Cleanup: ${BOLD}tmux kill-session -t $SESSION_NAME${NC}\n"
echo ""
