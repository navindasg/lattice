/**
 * Lattice Dashboard — Frontend Application
 *
 * Receives push updates from the Python backend via window.__latticeUpdate(),
 * renders all dashboard panels, and handles keyboard shortcuts.
 *
 * All state is immutable — each update replaces the previous snapshot entirely.
 * DOM updates are targeted (only changed elements are rewritten) to avoid
 * unnecessary reflows.
 */

/* ── State ─────────────────────────────────────────────────── */

/** @type {{ columns: number, interactive: boolean } | null} */
let config = null;

/** @type {object | null} */
let lastSnapshot = null;

/** @type {string | null} */
let focusedPaneId = null;

/** @type {'idle' | 'recording' | 'processing'} */
let micState = "idle";

/** @type {boolean} Whether the STT model has been loaded yet */
let sttModelLoaded = false;

/** @type {Array<{role: string, text: string}>} */
let voiceLogEntries = [];

const MAX_VOICE_LOG = 50;
const MAX_EVENTS_DISPLAY = 30;

/** @type {Map<string, string>} Cache of last rendered output HTML per pane */
const _lastOutputHtml = new Map();

/** @type {string} Cache of last rendered soul panel HTML */
let _lastSoulHtml = "";

/** @type {string} Cache of last rendered event log HTML */
let _lastEventHtml = "";


/* ── ANSI Parser ───────────────────────────────────────────── */

const ANSI_REGEX = /\x1b\[([0-9;]*)m/g;
const STRIP_NON_SGR = /\x1b\[[0-9;]*[A-HJKSTfhlr]|\x1b\][^\x07]*\x07|\x1b\[\?[0-9;]*[hl]/g;

const SGR_CLASSES = {
    0: null,        // reset
    1: "ansi-bold",
    2: "ansi-dim",
    3: "ansi-italic",
    4: "ansi-underline",
    30: "ansi-black", 31: "ansi-red", 32: "ansi-green", 33: "ansi-yellow",
    34: "ansi-blue", 35: "ansi-magenta", 36: "ansi-cyan", 37: "ansi-white",
    40: "ansi-bg-black", 41: "ansi-bg-red", 42: "ansi-bg-green", 43: "ansi-bg-yellow",
    44: "ansi-bg-blue", 45: "ansi-bg-magenta", 46: "ansi-bg-cyan", 47: "ansi-bg-white",
    90: "ansi-bright-black", 91: "ansi-bright-red", 92: "ansi-bright-green", 93: "ansi-bright-yellow",
    94: "ansi-bright-blue", 95: "ansi-bright-magenta", 96: "ansi-bright-cyan", 97: "ansi-bright-white",
};

/**
 * Convert a string with ANSI escape codes into safe HTML with CSS classes.
 * All text content is escaped to prevent XSS.
 *
 * @param {string} text - Raw text with ANSI escapes
 * @returns {string} HTML string with <span> wrappers for styled segments
 */
function ansiToHtml(text) {
    text = text.replace(STRIP_NON_SGR, "");
    const parts = [];
    let activeClasses = new Set();
    let lastIndex = 0;

    let match;
    ANSI_REGEX.lastIndex = 0;
    while ((match = ANSI_REGEX.exec(text)) !== null) {
        // Flush text before this escape
        if (match.index > lastIndex) {
            const segment = text.slice(lastIndex, match.index);
            parts.push(wrapWithClasses(escapeHtml(segment), activeClasses));
        }
        lastIndex = match.index + match[0].length;

        // Parse SGR parameters
        const params = match[1] ? match[1].split(";").map(Number) : [0];
        for (const code of params) {
            if (code === 0) {
                activeClasses = new Set();
            } else if (code === 22) {
                activeClasses.delete("ansi-bold");
                activeClasses.delete("ansi-dim");
            } else if (code === 23) {
                activeClasses.delete("ansi-italic");
            } else if (code === 24) {
                activeClasses.delete("ansi-underline");
            } else if (code >= 39 && code <= 39) {
                // Default foreground — remove all fg classes
                for (const cls of [...activeClasses]) {
                    if (cls.startsWith("ansi-") && !cls.startsWith("ansi-bg-") &&
                        !["ansi-bold", "ansi-dim", "ansi-italic", "ansi-underline"].includes(cls)) {
                        activeClasses.delete(cls);
                    }
                }
            } else if (code === 49) {
                // Default background — remove all bg classes
                for (const cls of [...activeClasses]) {
                    if (cls.startsWith("ansi-bg-")) {
                        activeClasses.delete(cls);
                    }
                }
            } else {
                const cls = SGR_CLASSES[code];
                if (cls) {
                    // Remove conflicting classes in same category
                    if (cls.startsWith("ansi-bg-")) {
                        for (const c of [...activeClasses]) {
                            if (c.startsWith("ansi-bg-")) activeClasses.delete(c);
                        }
                    } else if (!["ansi-bold", "ansi-dim", "ansi-italic", "ansi-underline"].includes(cls)) {
                        for (const c of [...activeClasses]) {
                            if (c.startsWith("ansi-") && !c.startsWith("ansi-bg-") &&
                                !["ansi-bold", "ansi-dim", "ansi-italic", "ansi-underline"].includes(c)) {
                                activeClasses.delete(c);
                            }
                        }
                    }
                    activeClasses.add(cls);
                }
            }
        }
    }

    // Flush remaining text
    if (lastIndex < text.length) {
        parts.push(wrapWithClasses(escapeHtml(text.slice(lastIndex)), activeClasses));
    }

    return parts.join("");
}

/**
 * @param {string} html - Already-escaped HTML
 * @param {Set<string>} classes
 * @returns {string}
 */
function wrapWithClasses(html, classes) {
    if (classes.size === 0) return html;
    return `<span class="${[...classes].join(" ")}">${html}</span>`;
}

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#x27;");
}


/* ── Initialization ────────────────────────────────────────── */

/**
 * Called by the Python backend when the window loads.
 * @param {object} cfg - { columns: number, interactive: boolean, poll_interval: number }
 */
window.__latticeInit = function(cfg) {
    config = cfg;
    const modeEl = document.getElementById("status-mode");
    if (modeEl) {
        modeEl.textContent = cfg.interactive ? "interactive" : "read-only";
    }
    applyGridColumns(cfg.columns);
};

/**
 * Called by the Python backend on every poll cycle with fresh data.
 * @param {object} snapshot - Serialized DashboardSnapshot
 */
window.__latticeUpdate = function(snapshot) {
    lastSnapshot = snapshot;
    renderAll(snapshot);
};


/* ── Rendering ─────────────────────────────────────────────── */

/**
 * Render all dashboard panels from a snapshot.
 * @param {object} snap
 */
function renderAll(snap) {
    renderTerminalGrid(snap.instances, snap.captured_output);
    renderSoulPanel(snap.soul_state, snap.memory_entries);
    renderEventLog(snap.recent_events);
    renderStatusBar(snap);
    renderHealthIndicator(snap.health);
}

/**
 * Apply grid column count via CSS.
 * @param {number} cols
 */
function applyGridColumns(cols) {
    const grid = document.getElementById("terminal-grid");
    if (!grid) return;
    const n = Math.max(1, Math.min(9, parseInt(cols, 10) || 3));
    grid.style.gridTemplateColumns = `repeat(${n}, 1fr)`;
}


/* ── Terminal Grid ─────────────────────────────────────────── */

/**
 * Render terminal panes into the grid.
 * @param {object[]} instances
 * @param {Object<string, string[]>} capturedOutput
 */
function renderTerminalGrid(instances, capturedOutput) {
    const grid = document.getElementById("terminal-grid");
    if (!grid) return;

    if (!instances || instances.length === 0) {
        grid.innerHTML = `
            <div id="grid-placeholder" class="placeholder">
                <div class="placeholder-icon">&#x1F4BB;</div>
                <div class="placeholder-text">No CC instances detected</div>
                <div class="placeholder-hint">Start Claude Code in a tmux session to see terminals here</div>
            </div>`;
        return;
    }

    const existingPanes = new Map();
    for (const el of grid.querySelectorAll(".terminal-pane")) {
        existingPanes.set(el.dataset.paneId, el);
    }

    const currentPaneIds = new Set(instances.map(i => i.pane_id));

    // Remove stale panes and their output caches
    for (const [paneId, el] of existingPanes) {
        if (!currentPaneIds.has(paneId)) {
            el.remove();
            _lastOutputHtml.delete(paneId);
        }
    }

    // Remove placeholder if present
    const placeholder = grid.querySelector("#grid-placeholder");
    if (placeholder) placeholder.remove();

    // Add or update panes
    for (const inst of instances) {
        const lines = capturedOutput[inst.pane_id] || [];
        const outputHtml = ansiToHtml(lines.join("\n"));
        const cwdBasename = inst.cwd.split("/").pop() || inst.cwd;
        const isFocused = focusedPaneId === inst.pane_id;

        let paneEl = existingPanes.get(inst.pane_id);
        if (!paneEl) {
            paneEl = document.createElement("div");
            paneEl.className = `terminal-pane${isFocused ? " focused" : ""}`;
            paneEl.dataset.paneId = inst.pane_id;
            paneEl.dataset.userNumber = String(inst.user_number);
            paneEl.addEventListener("click", () => {
                setFocusedPane(inst.pane_id);
            });
            paneEl.innerHTML = `
                <div class="pane-header">
                    <span class="pane-number">${escapeHtml(String(inst.user_number))}</span>
                    <span class="pane-label">CC #${escapeHtml(String(inst.user_number))}</span>
                    <span class="pane-id">${escapeHtml(inst.pane_id)}</span>
                    <span class="pane-cwd" title="${escapeHtml(inst.cwd)}">${escapeHtml(cwdBasename)}</span>
                </div>
                <div class="pane-output">${outputHtml}</div>`;
            grid.appendChild(paneEl);
        } else {
            // Update existing pane — skip DOM write if output unchanged
            paneEl.className = `terminal-pane${isFocused ? " focused" : ""}`;
            const outputEl = paneEl.querySelector(".pane-output");
            if (outputEl && outputHtml !== _lastOutputHtml.get(inst.pane_id)) {
                const wasScrolledToBottom = outputEl.scrollHeight - outputEl.scrollTop - outputEl.clientHeight < 20;
                _lastOutputHtml.set(inst.pane_id, outputHtml);
                outputEl.innerHTML = outputHtml;
                if (wasScrolledToBottom) {
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
            }
            // Update header fields that may change
            const cwdEl = paneEl.querySelector(".pane-cwd");
            if (cwdEl) {
                cwdEl.textContent = cwdBasename;
                cwdEl.title = inst.cwd;
            }
        }
    }
}

/**
 * Set focused pane by ID and update visual state.
 * @param {string} paneId
 */
function setFocusedPane(paneId) {
    focusedPaneId = paneId;
    for (const el of document.querySelectorAll(".terminal-pane")) {
        el.classList.toggle("focused", el.dataset.paneId === paneId);
    }
}

/**
 * Focus a pane by its user number (1-9).
 * @param {number} number
 */
function focusPaneByNumber(number) {
    const paneEl = document.querySelector(`.terminal-pane[data-user-number="${number}"]`);
    if (paneEl) {
        setFocusedPane(paneEl.dataset.paneId);
    }
}


/* ── Soul Panel ────────────────────────────────────────────── */

/**
 * Render the soul state panel.
 * @param {object} soulState
 * @param {object[]} memoryEntries
 */
function renderSoulPanel(soulState, memoryEntries) {
    const container = document.getElementById("soul-content");
    if (!container) return;

    const sections = [];

    // Instances
    sections.push(renderSoulSection("Instances", () => {
        if (!soulState.instances || soulState.instances.length === 0) {
            return `<div class="soul-empty">No active instances</div>`;
        }
        return soulState.instances.map(inst => {
            const dotClass = inst.status === "active" ? "dot-green"
                : inst.status === "idle" ? "dot-yellow"
                : "dot-red";
            return `<div class="soul-instance">
                <span class="status-dot ${dotClass}"></span>
                <span class="soul-instance-id">${escapeHtml(inst.instance_id)}</span>
                <span class="soul-instance-task" title="${escapeHtml(inst.task_description)}">${escapeHtml(inst.task_description)}</span>
            </div>`;
        }).join("");
    }));

    // Plan
    sections.push(renderSoulSection("Plan", () => {
        if (!soulState.plan || soulState.plan.length === 0) {
            return `<div class="soul-empty">No current plan</div>`;
        }
        return soulState.plan.map((item, i) =>
            `<div class="soul-item">${i + 1}. ${escapeHtml(item)}</div>`
        ).join("");
    }));

    // Blockers
    if (soulState.blockers && soulState.blockers.length > 0) {
        sections.push(renderSoulSection("Blockers", () => {
            return soulState.blockers.map(b =>
                `<div class="soul-item blocker">&#x26A0; ${escapeHtml(b)}</div>`
            ).join("");
        }));
    }

    // Decisions (last 5)
    sections.push(renderSoulSection("Recent Decisions", () => {
        if (!soulState.decisions || soulState.decisions.length === 0) {
            return `<div class="soul-empty">No recent decisions</div>`;
        }
        const recent = soulState.decisions.slice(-5);
        return recent.map(dec => {
            const icon = dec.event_type === "approve" ? "&#x2705;" : "&#x274C;";
            return `<div class="soul-decision">
                <span class="soul-decision-icon">${icon}</span>
                <span class="soul-decision-text" title="${escapeHtml(dec.target)}">${escapeHtml(dec.target)}</span>
            </div>`;
        }).join("");
    }));

    // Memory (last 8)
    sections.push(renderSoulSection("Memory", () => {
        if (!memoryEntries || memoryEntries.length === 0) {
            return `<div class="soul-empty">No memory entries</div>`;
        }
        const recent = memoryEntries.slice(-8);
        return recent.map(mem => {
            const ts = formatTimestamp(mem.timestamp);
            return `<div class="soul-memory">
                <span class="soul-memory-ts">${escapeHtml(ts)}</span>
                <span class="soul-memory-cat">[${escapeHtml(mem.category)}]</span>
                <span class="soul-memory-content">${escapeHtml(mem.content)}</span>
            </div>`;
        }).join("");
    }));

    const html = sections.join("");
    if (html !== _lastSoulHtml) {
        _lastSoulHtml = html;
        container.innerHTML = html;
    }
}

/**
 * Helper to render a soul section with title and content.
 * @param {string} title
 * @param {() => string} contentFn
 * @returns {string}
 */
function renderSoulSection(title, contentFn) {
    return `<div class="soul-section">
        <div class="soul-section-title">${escapeHtml(title)}</div>
        ${contentFn()}
    </div>`;
}


/* ── Event Log ─────────────────────────────────────────────── */

const EVENT_TYPE_CLASS_MAP = {
    PreToolUse: "event-type-pre",
    PostToolUse: "event-type-post",
    SessionStart: "event-type-start",
    Stop: "event-type-stop",
    Notification: "event-type-notify",
};

/**
 * Render the event log panel.
 * @param {object[]} events
 */
function renderEventLog(events) {
    const container = document.getElementById("event-log");
    const countBadge = document.getElementById("event-count");
    if (!container) return;

    if (countBadge) {
        countBadge.textContent = String(events ? events.length : 0);
    }

    if (!events || events.length === 0) {
        container.innerHTML = `<div class="soul-empty">No events yet</div>`;
        return;
    }

    const recent = events.slice(-MAX_EVENTS_DISPLAY);

    const eventsHtml = recent.map(ev => {
        const ts = formatTimestamp(ev.timestamp || "");
        const eventType = ev.event_type || "Unknown";
        const typeClass = EVENT_TYPE_CLASS_MAP[eventType] || "event-type-default";
        const tool = ev.tool_name || "";
        const sessionShort = (ev.session_id || "").slice(0, 8);

        return `<div class="event-entry">
            <span class="event-ts">${escapeHtml(ts)}</span>
            <span class="event-type ${typeClass}">${escapeHtml(eventType)}</span>
            <span class="event-tool">${escapeHtml(tool)}</span>
            <span class="event-session">(${escapeHtml(sessionShort)})</span>
        </div>`;
    }).join("");

    if (eventsHtml !== _lastEventHtml) {
        const wasScrolledToBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 20;
        _lastEventHtml = eventsHtml;
        container.innerHTML = eventsHtml;
        if (wasScrolledToBottom) {
            container.scrollTop = container.scrollHeight;
        }
    }
}


/* ── Status Bar ────────────────────────────────────────────── */

/**
 * Render the status bar with counts and health info.
 * @param {object} snap
 */
function renderStatusBar(snap) {
    const instanceCount = snap.instances ? snap.instances.length : 0;
    const eventCount = snap.recent_events ? snap.recent_events.length : 0;
    const planCount = snap.soul_state && snap.soul_state.plan ? snap.soul_state.plan.length : 0;
    const blockerCount = snap.soul_state && snap.soul_state.blockers ? snap.soul_state.blockers.length : 0;

    updateStatusItem("status-instances", `${instanceCount} instance${instanceCount !== 1 ? "s" : ""}`,
        instanceCount > 0 ? "dot-green" : "dot-dim");
    updateStatusItem("status-events", `${eventCount} events`, "dot-cyan");
    updateStatusItem("status-plan", `${planCount} plan items`, "dot-blue");

    const blockerWrapper = document.getElementById("status-blockers-wrapper");
    if (blockerWrapper) {
        if (blockerCount > 0) {
            blockerWrapper.classList.remove("hidden");
            const blockerText = blockerWrapper.querySelector("#status-blockers");
            if (blockerText) blockerText.textContent = `${blockerCount} blockers`;
        } else {
            blockerWrapper.classList.add("hidden");
        }
    }

    // Health in status
    const healthEl = document.getElementById("status-health");
    if (healthEl && snap.health) {
        const uptime = formatUptime(snap.health.uptime_seconds || 0);
        const sessions = snap.health.connected_sessions || 0;
        healthEl.textContent = snap.health.uptime_seconds > 0
            ? `up ${uptime} \u2022 ${sessions} session${sessions !== 1 ? "s" : ""}`
            : "";
    }
}

/**
 * Update a status bar item's text and dot color.
 * @param {string} id
 * @param {string} text
 * @param {string} dotClass
 */
function updateStatusItem(id, text, dotClass) {
    const el = document.getElementById(id);
    if (!el) return;
    const dot = el.querySelector(".status-dot");
    const span = el.querySelector("span:last-child");
    if (dot) {
        dot.className = `status-dot ${dotClass}`;
    }
    if (span) {
        span.textContent = text;
    }
}

/**
 * Update the health indicator dot in the title bar.
 * @param {object} health
 */
function renderHealthIndicator(health) {
    const dot = document.getElementById("health-indicator");
    if (!dot) return;
    if (health && health.uptime_seconds > 0) {
        dot.className = "health-dot health-connected";
        dot.title = `Connected \u2022 Uptime: ${formatUptime(health.uptime_seconds)}`;
    } else {
        dot.className = "health-dot health-disconnected";
        dot.title = "Disconnected";
    }
}


/* ── Voice Controls ────────────────────────────────────────── */

function initVoiceControls() {
    const micBtn = document.getElementById("mic-btn");
    const textInput = document.getElementById("voice-text-input");

    if (micBtn) {
        micBtn.addEventListener("click", toggleMic);
    }

    if (textInput) {
        textInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                const text = textInput.value.trim();
                if (text) {
                    submitTextCommand(text);
                    textInput.value = "";
                }
            }
            e.stopPropagation();
        });
    }
}

/**
 * Toggle mic: idle -> start recording, recording -> stop and process.
 */
async function toggleMic() {
    if (micState === "processing") return;

    if (micState === "idle") {
        setMicState("recording");
        addChatMessage("system", "Starting recording...");

        try {
            const resJson = await window.pywebview.api.start_recording();
            const res = typeof resJson === "string" ? JSON.parse(resJson) : resJson;
            if (!res.success) {
                addChatMessage("error", res.error || "Failed to start recording");
                setMicState("idle");
                return;
            }
            addChatMessage("system", "Listening... click again or press V to stop");
        } catch (err) {
            addChatMessage("error", `Mic error: ${err.message || String(err)}`);
            setMicState("idle");
        }
    } else if (micState === "recording") {
        setMicState("processing");
        if (!sttModelLoaded) {
            addChatMessage("system", "Loading speech model (first time only)...");
        } else {
            addChatMessage("system", "Processing speech...");
        }

        try {
            const resJson = await window.pywebview.api.stop_recording();
            const res = typeof resJson === "string" ? JSON.parse(resJson) : resJson;

            sttModelLoaded = true;

            if (res.transcript) {
                addChatMessage("user", res.transcript);
            }

            if (res.success) {
                addChatMessage("assistant", `${res.action}: ${res.detail}`);
            } else {
                addChatMessage("error", res.detail || res.action || "Unknown error");
            }
        } catch (err) {
            addChatMessage("error", `Processing error: ${err.message || String(err)}`);
        } finally {
            setMicState("idle");
        }
    }
}

/**
 * @param {'idle' | 'recording' | 'processing'} state
 */
function setMicState(state) {
    micState = state;
    const btn = document.getElementById("mic-btn");
    if (!btn) return;

    btn.className = `mic-button mic-${state}`;
    const label = btn.querySelector(".mic-label");
    if (label) {
        const labels = {
            idle: "Push to Talk",
            recording: "Recording...",
            processing: "Processing...",
        };
        label.textContent = labels[state] || "Push to Talk";
    }
    const icon = btn.querySelector(".mic-icon");
    if (icon) {
        const icons = {
            idle: "\u{1F3A4}",
            recording: "\u{1F534}",
            processing: "\u23F3",
        };
        icon.textContent = icons[state] || "\u{1F3A4}";
    }
}

/**
 * Submit a text command to the backend.
 * @param {string} text
 */
async function submitTextCommand(text) {
    addChatMessage("user", text);
    setMicState("processing");

    try {
        const resultJson = await window.pywebview.api.send_text_command(text);
        const result = typeof resultJson === "string" ? JSON.parse(resultJson) : resultJson;

        if (result.success) {
            addChatMessage("assistant", `${result.action}: ${result.detail}`);
        } else {
            addChatMessage("error", result.detail || result.action || "Unknown error");
        }
    } catch (err) {
        addChatMessage("error", err.message || String(err));
    } finally {
        setMicState("idle");
    }
}

/**
 * Add a chat message to the voice log.
 * @param {'user' | 'assistant' | 'system' | 'error'} role
 * @param {string} text
 */
function addChatMessage(role, text) {
    voiceLogEntries = [...voiceLogEntries, { role, text }].slice(-MAX_VOICE_LOG);
    renderVoiceLog();
}

function renderVoiceLog() {
    const container = document.getElementById("voice-log");
    if (!container) return;

    container.innerHTML = voiceLogEntries.map(entry => {
        const role = entry.role || "system";
        const roleLabel = { user: "You", assistant: "Lattice", system: "", error: "" }[role] || "";
        const prefix = roleLabel ? `<span class="chat-role chat-role-${role}">${escapeHtml(roleLabel)}</span> ` : "";
        return `<div class="chat-msg chat-${role}">${prefix}${escapeHtml(entry.text)}</div>`;
    }).join("");

    container.scrollTop = container.scrollHeight;
}


/* ── Keyboard Shortcuts ────────────────────────────────────── */

function initKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
        // Don't intercept when typing in an input
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

        const key = e.key.toLowerCase();

        switch (key) {
            case "r":
                e.preventDefault();
                forceRefresh();
                break;
            case "t":
                e.preventDefault();
                focusTextInput();
                break;
            case "v":
                e.preventDefault();
                toggleMic();
                break;
            case "?":
                e.preventDefault();
                toggleShortcutsOverlay();
                break;
            case "escape":
                closeOverlays();
                break;
            default:
                if (/^[1-9]$/.test(key)) {
                    e.preventDefault();
                    focusPaneByNumber(parseInt(key, 10));
                }
        }
    });
}

async function forceRefresh() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
        const resultJson = await window.pywebview.api.poll_snapshot();
        const result = typeof resultJson === "string" ? JSON.parse(resultJson) : resultJson;
        if (result && !result.error) {
            lastSnapshot = result;
            renderAll(result);
        }
    } catch (err) {
        // Refresh failed silently — next poll cycle will update
    }
}

function focusTextInput() {
    const input = document.getElementById("voice-text-input");
    if (input) input.focus();
}

function toggleShortcutsOverlay() {
    const overlay = document.getElementById("shortcuts-overlay");
    if (overlay) overlay.classList.toggle("hidden");
}

function closeOverlays() {
    const overlay = document.getElementById("shortcuts-overlay");
    if (overlay) overlay.classList.add("hidden");
}


/* ── Utility ───────────────────────────────────────────────── */

/**
 * Format an ISO timestamp to a short time string (HH:MM:SS).
 * @param {string} ts - ISO 8601 timestamp
 * @returns {string}
 */
function formatTimestamp(ts) {
    if (!ts) return "";
    try {
        const date = new Date(ts);
        if (isNaN(date.getTime())) return ts.slice(0, 8);
        return date.toLocaleTimeString("en-US", {
            hour12: false,
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    } catch {
        return ts.slice(0, 8);
    }
}

/**
 * Format seconds into a human-readable uptime string.
 * @param {number} seconds
 * @returns {string}
 */
function formatUptime(seconds) {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
}


/* ── Bootstrap ─────────────────────────────────────────────── */

document.addEventListener("DOMContentLoaded", () => {
    initVoiceControls();
    initKeyboardShortcuts();
});
