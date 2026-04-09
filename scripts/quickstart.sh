#!/usr/bin/env bash
#
# quickstart.sh — Automate testing the Lattice orchestrator on real code.
#
# Sets up a tmux session, initializes the soul ecosystem, installs hooks,
# starts the orchestrator, and optionally launches the TUI dashboard.
#
# Usage:
#   ./quickstart.sh [OPTIONS] [PROJECT_DIR]
#
# Arguments:
#   PROJECT_DIR          Target project directory (default: current directory)
#
# Options:
#   --text "message"     Send a one-shot text command instead of starting voice
#   --dashboard          Launch the TUI dashboard after starting orchestrator
#   --dashboard-only     Launch only the TUI dashboard (skip orchestrator start)
#   --cols N             Number of terminal grid columns for dashboard (default: 3)
#   --teardown           Uninstall hooks and kill the lattice-orch tmux session
#   --no-voice           Start orchestrator without the voice listener
#   --session NAME       Override tmux session name (default: lattice-orch)
#   --help               Show this help message
#
# Prerequisites:
#   - tmux
#   - python 3.12+
#   - lattice CLI (pip install -e packages/lattice)
#   - claude CLI (for hook integration)
#
# Examples:
#   # Start orchestrator with voice on current directory
#   ./quickstart.sh
#
#   # Start orchestrator on a specific project
#   ./quickstart.sh ~/projects/my-app
#
#   # Launch the TUI dashboard after starting the orchestrator
#   ./quickstart.sh --dashboard
#
#   # Launch only the dashboard (orchestrator already running)
#   ./quickstart.sh --dashboard-only
#
#   # Dashboard with 2-column grid
#   ./quickstart.sh --dashboard --cols 2
#
#   # Quick text test without voice hardware
#   ./quickstart.sh --text "show me all Python files with errors"
#
#   # Text test targeting a specific project
#   ./quickstart.sh --text "list recent commits" ~/projects/my-app
#
#   # Clean up everything
#   ./quickstart.sh --teardown
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SESSION_NAME="lattice-orch"
TEXT_MSG=""
TEARDOWN=false
VOICE=true
DASHBOARD=false
DASHBOARD_ONLY=false
DASH_COLS=3
PROJECT_DIR=""

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'  # No Color

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
err()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    sed -n '2,/^$/{ s/^# //; s/^#//; p; }' "$0"
    exit 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            usage
            ;;
        --text)
            if [[ -z "${2:-}" ]]; then
                err "--text requires a message argument"
                exit 1
            fi
            TEXT_MSG="$2"
            shift 2
            ;;
        --teardown)
            TEARDOWN=true
            shift
            ;;
        --dashboard)
            DASHBOARD=true
            shift
            ;;
        --dashboard-only)
            DASHBOARD_ONLY=true
            shift
            ;;
        --cols)
            if [[ -z "${2:-}" ]]; then
                err "--cols requires a number argument"
                exit 1
            fi
            DASH_COLS="$2"
            shift 2
            ;;
        --no-voice)
            VOICE=false
            shift
            ;;
        --session)
            if [[ -z "${2:-}" ]]; then
                err "--session requires a name argument"
                exit 1
            fi
            SESSION_NAME="$2"
            shift 2
            ;;
        -*)
            err "Unknown option: $1"
            exit 1
            ;;
        *)
            if [[ -n "$PROJECT_DIR" ]]; then
                err "Only one project directory argument is allowed"
                exit 1
            fi
            PROJECT_DIR="$1"
            shift
            ;;
    esac
done

# Resolve project directory
if [[ -z "$PROJECT_DIR" ]]; then
    PROJECT_DIR="$(pwd)"
fi
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
check_prerequisites() {
    local missing=0

    info "Checking prerequisites..."

    if command -v tmux &>/dev/null; then
        ok "tmux $(tmux -V 2>/dev/null || echo '(version unknown)')"
    else
        err "tmux is not installed. Install with: brew install tmux"
        missing=1
    fi

    if command -v python3 &>/dev/null; then
        local py_version
        py_version="$(python3 --version 2>&1)"
        ok "$py_version"
    else
        err "python3 is not installed"
        missing=1
    fi

    if command -v lattice &>/dev/null; then
        ok "lattice CLI found at $(command -v lattice)"
    else
        err "lattice CLI not found. Install with: pip install -e packages/lattice"
        missing=1
    fi

    if command -v claude &>/dev/null; then
        ok "claude CLI found at $(command -v claude)"
    else
        warn "claude CLI not found — hook integration will not work"
    fi

    if [[ ! -d "$PROJECT_DIR" ]]; then
        err "Project directory does not exist: $PROJECT_DIR"
        missing=1
    fi

    if [[ "$missing" -eq 1 ]]; then
        err "Missing prerequisites. Fix the errors above and retry."
        exit 1
    fi

    ok "All prerequisites satisfied"
}

# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------
do_teardown() {
    info "Tearing down Lattice orchestrator..."

    # Uninstall hooks
    if command -v lattice &>/dev/null; then
        info "Uninstalling hooks..."
        if lattice orchestrator:uninstall-hooks 2>/dev/null; then
            ok "Hooks uninstalled"
        else
            warn "Hook uninstall returned non-zero (may already be clean)"
        fi
    else
        warn "lattice CLI not found — skipping hook uninstall"
    fi

    # Kill tmux session
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        info "Killing tmux session: $SESSION_NAME"
        tmux kill-session -t "$SESSION_NAME"
        ok "Session killed"
    else
        info "No tmux session named '$SESSION_NAME' found"
    fi

    # Clean up stale socket
    local sock_path="$HOME/.lattice/orchestrator.sock"
    if [[ -S "$sock_path" ]]; then
        info "Removing stale socket: $sock_path"
        rm -f "$sock_path"
        ok "Socket removed"
    fi

    ok "Teardown complete"
    exit 0
}

# ---------------------------------------------------------------------------
# Text mode (one-shot, no tmux needed)
# ---------------------------------------------------------------------------
do_text() {
    local msg="$1"
    info "Sending text command to orchestrator..."
    info "Project: $PROJECT_DIR"
    info "Message: $msg"
    echo ""

    cd "$PROJECT_DIR"
    lattice orchestrator:voice --text "$msg" --project "$PROJECT_DIR"
}

# ---------------------------------------------------------------------------
# Full orchestrator startup
# ---------------------------------------------------------------------------
do_start() {
    info "Starting Lattice orchestrator"
    info "Project:  $PROJECT_DIR"
    info "Session:  $SESSION_NAME"
    info "Voice:    $VOICE"
    echo ""

    # Step 1: Initialize soul ecosystem
    info "Initializing soul ecosystem..."
    cd "$PROJECT_DIR"
    lattice orchestrator:init
    ok "Soul ecosystem initialized"

    # Step 2: Install hooks
    info "Installing hooks..."
    if lattice orchestrator:install-hooks; then
        ok "Hooks installed"
    else
        warn "Hook installation had issues (continuing anyway)"
    fi

    # Step 3: Verify hooks
    info "Checking hook status..."
    lattice orchestrator:check-hooks || true
    echo ""

    # Step 4: Create tmux session and start orchestrator
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        warn "tmux session '$SESSION_NAME' already exists"
        printf "  Kill it and start fresh? [y/N] "
        read -r answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            tmux kill-session -t "$SESSION_NAME"
            ok "Old session killed"
        else
            err "Aborting. Use --teardown to clean up first."
            exit 1
        fi
    fi

    info "Creating tmux session: $SESSION_NAME"
    tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_DIR"
    ok "tmux session created"

    # Build the orchestrator command
    local orch_cmd="lattice orchestrator:voice --project '$PROJECT_DIR'"
    if [[ "$VOICE" == "false" ]]; then
        orch_cmd="lattice orchestrator:start"
    fi

    # Send the command to the tmux session
    info "Launching orchestrator in tmux..."
    tmux send-keys -t "$SESSION_NAME" "$orch_cmd" Enter

    echo ""
    printf "${BOLD}${GREEN}Lattice orchestrator is starting in tmux session '%s'${NC}\n" "$SESSION_NAME"
    echo ""
    echo "Useful commands:"
    echo "  tmux attach -t $SESSION_NAME          Attach to the session"
    echo "  lattice orchestrator:status            Check instance status"
    echo "  lattice orchestrator:check-hooks       Verify hook installation"
    echo "  lattice ui:dashboard                   Launch the TUI dashboard"
    echo "  $0 --text \"your command\"       Send a text command"
    echo "  $0 --teardown                  Clean up everything"
    echo ""
}

# ---------------------------------------------------------------------------
# TUI Dashboard
# ---------------------------------------------------------------------------
do_dashboard() {
    info "Launching TUI dashboard"
    info "Project:  $PROJECT_DIR"
    info "Columns:  $DASH_COLS"
    echo ""

    cd "$PROJECT_DIR"
    lattice ui:dashboard --cols "$DASH_COLS"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ "$TEARDOWN" == "true" ]]; then
    do_teardown
fi

check_prerequisites

if [[ "$DASHBOARD_ONLY" == "true" ]]; then
    do_dashboard
elif [[ -n "$TEXT_MSG" ]]; then
    do_text "$TEXT_MSG"
else
    do_start
    if [[ "$DASHBOARD" == "true" ]]; then
        # Give orchestrator a moment to start before launching dashboard
        sleep 2
        do_dashboard
    fi
fi
