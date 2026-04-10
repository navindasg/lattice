/**
 * Lattice Dashboard — Frontend Application
 *
 * Receives push updates from the Python backend via window.__latticeUpdate(),
 * renders all dashboard panels, and manages xterm.js terminal instances.
 *
 * Terminal output is pushed from the backend via window.__terminalOutput()
 * as base64-encoded data.  Keyboard input flows back via
 * window.pywebview.api.write_terminal().
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

/** @type {string} Cache of last rendered soul panel HTML */
let _lastSoulHtml = "";

/** @type {string} Cache of last rendered event log HTML */
let _lastEventHtml = "";

/**
 * @typedef {Object} ManagedTerminal
 * @property {Terminal} xterm - xterm.js Terminal instance
 * @property {FitAddon} fitAddon - Fit addon instance
 * @property {HTMLElement} container - DOM container element
 * @property {string} paneId - Unique terminal identifier
 * @property {boolean} dead - Whether the terminal process has exited
 * @property {number|null} exitCode - Exit code if dead
 * @property {number} userNumber - User-facing number (1-9)
 */

/** @type {Map<string, ManagedTerminal>} Active terminal instances */
const terminals = new Map();

/** @type {number} Next user-facing terminal number */
let nextTerminalNumber = 1;

/** @type {Map<number, string>} User number to pane_id mapping */
const numberToPaneId = new Map();


/* ── ANSI Helper (kept for non-terminal text like soul panel) ─ */

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


/* ── Binary Encoding Helpers ──────────────────────────────── */

/**
 * Encode a Uint8Array to a base64 string.
 * Avoids the spread-operator stack overflow that btoa(String.fromCharCode(...arr))
 * causes on large arrays (>65k elements).
 *
 * @param {Uint8Array} bytes
 * @returns {string} Base64-encoded string
 */
function bytesToBase64(bytes) {
    let binary = "";
    const len = bytes.length;
    for (let i = 0; i < len; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

/**
 * Decode a base64 string to a Uint8Array.
 *
 * @param {string} b64
 * @returns {Uint8Array}
 */
function base64ToBytes(b64) {
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) {
        bytes[i] = raw.charCodeAt(i);
    }
    return bytes;
}


/* ── Terminal Management (xterm.js) ───────────────────────── */

/**
 * Spawn a new terminal via the backend PTYManager and create an
 * xterm.js instance to render it.
 *
 * @param {Object} [opts] - Options for the new terminal.
 * @param {string[]} [opts.cmd] - Command to run (default: user shell).
 * @param {string} [opts.cwd] - Working directory.
 * @returns {Promise<string|null>} The pane_id, or null on failure.
 */
async function spawnTerminal(opts) {
    if (!window.pywebview || !window.pywebview.api) return null;

    const grid = document.getElementById("terminal-grid");
    if (!grid) return null;

    // Remove placeholder if present
    const placeholder = grid.querySelector("#grid-placeholder");
    if (placeholder) placeholder.remove();

    // Create DOM structure for this pane
    const userNumber = nextTerminalNumber;
    const paneEl = document.createElement("div");
    paneEl.className = "terminal-pane";

    const header = document.createElement("div");
    header.className = "pane-header";
    header.innerHTML = `
        <span class="pane-number">${userNumber}</span>
        <span class="pane-label">Terminal #${userNumber}</span>
        <span class="pane-status-indicator pane-status-alive"></span>
        <span class="pane-cwd"></span>
        <button class="pane-close-btn" title="Close terminal">&times;</button>
    `;
    paneEl.appendChild(header);

    const termContainer = document.createElement("div");
    termContainer.className = "xterm-container";
    paneEl.appendChild(termContainer);

    // Dead terminal overlay (hidden initially)
    const deadOverlay = document.createElement("div");
    deadOverlay.className = "dead-overlay hidden";
    deadOverlay.innerHTML = `<span class="dead-text">[exited]</span>`;
    paneEl.appendChild(deadOverlay);

    grid.appendChild(paneEl);

    // Create xterm.js instance
    const xterm = new Terminal({
        cursorBlink: true,
        cursorStyle: "block",
        fontFamily: '"SF Mono", "Fira Code", "Cascadia Code", "JetBrains Mono", monospace',
        fontSize: 10,
        lineHeight: 1.2,
        scrollback: 5000,
        theme: {
            background: "#0f1117",
            foreground: "#e4e6ed",
            cursor: "#4f8ff7",
            cursorAccent: "#0f1117",
            selectionBackground: "rgba(79, 143, 247, 0.3)",
            selectionForeground: "#e4e6ed",
            black: "#3b4048",
            red: "#e55353",
            green: "#3ecf71",
            yellow: "#f5a623",
            blue: "#4f8ff7",
            magenta: "#c084fc",
            cyan: "#59c9e8",
            white: "#e4e6ed",
            brightBlack: "#5c6070",
            brightRed: "#ff7070",
            brightGreen: "#5eff8a",
            brightYellow: "#ffc44c",
            brightBlue: "#80b5ff",
            brightMagenta: "#dda0ff",
            brightCyan: "#7ee0f5",
            brightWhite: "#ffffff",
        },
        allowProposedApi: true,
    });

    const fitAddon = new FitAddon.FitAddon();
    xterm.loadAddon(fitAddon);

    // Load web links addon for clickable URLs
    try {
        const webLinksAddon = new WebLinksAddon.WebLinksAddon();
        xterm.loadAddon(webLinksAddon);
    } catch {
        // Web links addon is optional
    }

    xterm.open(termContainer);

    // Fit to container after DOM is rendered
    requestAnimationFrame(() => {
        fitAddon.fit();
    });

    // Request backend to spawn the PTY process
    const spawnParams = {};
    if (opts && opts.cmd) spawnParams.cmd = opts.cmd;
    if (opts && opts.cwd) spawnParams.cwd = opts.cwd;
    spawnParams.cols = xterm.cols;
    spawnParams.rows = xterm.rows;

    let paneId;
    try {
        const resJson = await window.pywebview.api.spawn_terminal(JSON.stringify(spawnParams));
        const res = typeof resJson === "string" ? JSON.parse(resJson) : resJson;
        if (!res.success) {
            xterm.writeln(`\r\n\x1b[31mFailed to spawn terminal: ${res.error}\x1b[0m`);
            return null;
        }
        paneId = res.pane_id;
    } catch (err) {
        xterm.writeln(`\r\n\x1b[31mSpawn error: ${err.message || String(err)}\x1b[0m`);
        return null;
    }

    paneEl.dataset.paneId = paneId;
    paneEl.dataset.userNumber = String(userNumber);

    // Store the managed terminal
    const managed = {
        xterm,
        fitAddon,
        container: paneEl,
        paneId,
        dead: false,
        exitCode: null,
        userNumber,
    };
    terminals.set(paneId, managed);
    numberToPaneId.set(userNumber, paneId);
    nextTerminalNumber++;

    // Wire keyboard input: xterm → backend
    // Use TextEncoder for safe UTF-8 → base64 encoding (btoa alone
    // throws on characters above U+00FF such as emoji or CJK).
    xterm.onData((data) => {
        if (managed.dead) return;
        const encoder = new TextEncoder();
        const bytes = encoder.encode(data);
        const b64 = bytesToBase64(bytes);
        window.pywebview.api.write_terminal(paneId, b64).catch(() => {});
    });

    // Wire binary input for special keys
    xterm.onBinary((data) => {
        if (managed.dead) return;
        const bytes = new Uint8Array(data.length);
        for (let i = 0; i < data.length; i++) {
            bytes[i] = data.charCodeAt(i);
        }
        const b64 = bytesToBase64(bytes);
        window.pywebview.api.write_terminal(paneId, b64).catch(() => {});
    });

    // Wire resize: fit addon → backend
    xterm.onResize(({ cols, rows }) => {
        if (managed.dead) return;
        window.pywebview.api.resize_terminal(paneId, cols, rows).catch(() => {});
    });

    // Click to focus
    paneEl.addEventListener("click", (e) => {
        // Don't focus if clicking close button
        if (e.target.closest(".pane-close-btn")) return;
        setFocusedPane(paneId);
    });

    // Close button
    const closeBtn = paneEl.querySelector(".pane-close-btn");
    if (closeBtn) {
        closeBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            killTerminal(paneId);
        });
    }

    // Focus this new terminal
    setFocusedPane(paneId);

    // Update terminal count in status bar
    updateTerminalCount();

    return paneId;
}

/**
 * Create an xterm.js pane for an externally-spawned terminal (e.g. via cc_spawn).
 * Does NOT call spawn_terminal on the backend — the process already exists.
 *
 * @param {string} paneId - The backend pane_id to attach to.
 * @returns {ManagedTerminal|null} The created managed terminal, or null on failure.
 */
function createTerminalPane(paneId) {
    const grid = document.getElementById("terminal-grid");
    if (!grid) return null;

    // Remove placeholder if present
    const placeholder = grid.querySelector("#grid-placeholder");
    if (placeholder) placeholder.remove();

    const userNumber = nextTerminalNumber;
    const paneEl = document.createElement("div");
    paneEl.className = "terminal-pane";
    paneEl.dataset.paneId = paneId;
    paneEl.dataset.userNumber = String(userNumber);

    const header = document.createElement("div");
    header.className = "pane-header";
    header.innerHTML = `
        <span class="pane-number">${userNumber}</span>
        <span class="pane-label">CC #${userNumber}</span>
        <span class="pane-status-indicator pane-status-alive"></span>
        <span class="pane-cwd"></span>
        <button class="pane-close-btn" title="Close terminal">&times;</button>
    `;
    paneEl.appendChild(header);

    const termContainer = document.createElement("div");
    termContainer.className = "xterm-container";
    paneEl.appendChild(termContainer);

    const deadOverlay = document.createElement("div");
    deadOverlay.className = "dead-overlay hidden";
    deadOverlay.innerHTML = `<span class="dead-text">[exited]</span>`;
    paneEl.appendChild(deadOverlay);

    grid.appendChild(paneEl);

    const xterm = new Terminal({
        cursorBlink: true,
        cursorStyle: "block",
        fontFamily: '"SF Mono", "Fira Code", "Cascadia Code", "JetBrains Mono", monospace',
        fontSize: 10,
        lineHeight: 1.2,
        scrollback: 5000,
        theme: {
            background: "#0f1117",
            foreground: "#e4e6ed",
            cursor: "#4f8ff7",
            cursorAccent: "#0f1117",
            selectionBackground: "rgba(79, 143, 247, 0.3)",
            selectionForeground: "#e4e6ed",
            black: "#3b4048",
            red: "#e55353",
            green: "#3ecf71",
            yellow: "#f5a623",
            blue: "#4f8ff7",
            magenta: "#c084fc",
            cyan: "#59c9e8",
            white: "#e4e6ed",
            brightBlack: "#5c6070",
            brightRed: "#ff7070",
            brightGreen: "#5eff8a",
            brightYellow: "#ffc44c",
            brightBlue: "#80b5ff",
            brightMagenta: "#dda0ff",
            brightCyan: "#7ee0f5",
            brightWhite: "#ffffff",
        },
        allowProposedApi: true,
    });

    const fitAddon = new FitAddon.FitAddon();
    xterm.loadAddon(fitAddon);

    try {
        const webLinksAddon = new WebLinksAddon.WebLinksAddon();
        xterm.loadAddon(webLinksAddon);
    } catch {
        // optional
    }

    xterm.open(termContainer);
    requestAnimationFrame(() => fitAddon.fit());

    const managed = {
        xterm,
        fitAddon,
        container: paneEl,
        paneId,
        dead: false,
        exitCode: null,
        userNumber,
    };
    terminals.set(paneId, managed);
    numberToPaneId.set(userNumber, paneId);
    nextTerminalNumber++;

    // Wire keyboard input → backend
    xterm.onData((data) => {
        if (managed.dead) return;
        if (!window.pywebview || !window.pywebview.api) return;
        const encoder = new TextEncoder();
        const bytes = encoder.encode(data);
        const b64 = bytesToBase64(bytes);
        window.pywebview.api.write_terminal(paneId, b64).catch(() => {});
    });

    xterm.onBinary((data) => {
        if (managed.dead) return;
        if (!window.pywebview || !window.pywebview.api) return;
        const bytes = new Uint8Array(data.length);
        for (let i = 0; i < data.length; i++) bytes[i] = data.charCodeAt(i);
        const b64 = bytesToBase64(bytes);
        window.pywebview.api.write_terminal(paneId, b64).catch(() => {});
    });

    xterm.onResize(({ cols, rows }) => {
        if (managed.dead) return;
        if (!window.pywebview || !window.pywebview.api) return;
        window.pywebview.api.resize_terminal(paneId, cols, rows).catch(() => {});
    });

    paneEl.addEventListener("click", (e) => {
        if (e.target.closest(".pane-close-btn")) return;
        setFocusedPane(paneId);
    });

    const closeBtn = paneEl.querySelector(".pane-close-btn");
    if (closeBtn) {
        closeBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            killTerminal(paneId);
        });
    }

    updateTerminalCount();
    return managed;
}

/**
 * Kill a terminal and remove it from the grid.
 * @param {string} paneId
 */
async function killTerminal(paneId) {
    const managed = terminals.get(paneId);
    if (!managed) return;

    // Tell backend to kill the PTY
    if (!managed.dead && window.pywebview && window.pywebview.api) {
        try {
            await window.pywebview.api.kill_terminal(paneId);
        } catch {
            // May already be dead
        }
    }

    // Clean up xterm instance
    managed.xterm.dispose();

    // Remove from DOM
    managed.container.remove();

    // Clean up maps
    terminals.delete(paneId);
    numberToPaneId.delete(managed.userNumber);

    // Unfocus if this was the focused pane
    if (focusedPaneId === paneId) {
        focusedPaneId = null;
        // Focus the next available terminal
        const remaining = Array.from(terminals.values());
        if (remaining.length > 0) {
            setFocusedPane(remaining[0].paneId);
        }
    }

    // Show placeholder if no terminals left
    if (terminals.size === 0) {
        showGridPlaceholder();
    }

    updateTerminalCount();
}

/**
 * Show the empty grid placeholder.
 */
function showGridPlaceholder() {
    const grid = document.getElementById("terminal-grid");
    if (!grid) return;
    if (grid.querySelector("#grid-placeholder")) return;

    grid.innerHTML = `
        <div id="grid-placeholder" class="placeholder">
            <div class="placeholder-icon">&#x1F4BB;</div>
            <div class="placeholder-text">No terminals running</div>
            <div class="placeholder-hint">Press <kbd>N</kbd> or click <strong>+</strong> to open a terminal</div>
        </div>`;
}

/**
 * Receive terminal output from the Python backend.
 * Called via evaluate_js from the PTYManager output callback.
 *
 * @param {string} paneId - Terminal identifier.
 * @param {string} b64Data - Base64-encoded output bytes.
 */
window.__terminalOutput = function(paneId, b64Data) {
    let managed = terminals.get(paneId);

    // Auto-create xterm instance for terminals spawned by the orchestrator
    // (e.g. via cc_spawn) that the frontend doesn't know about yet
    if (!managed) {
        managed = createTerminalPane(paneId);
        if (!managed) return;
    }

    managed.xterm.write(base64ToBytes(b64Data));
};

/**
 * Handle terminal process exit notification from the backend.
 * Called via evaluate_js from the PTYManager exit callback.
 *
 * @param {string} paneId - Terminal identifier.
 * @param {number|null} exitCode - Process exit code.
 */
window.__terminalExited = function(paneId, exitCode) {
    const managed = terminals.get(paneId);
    if (!managed) return;

    managed.dead = true;
    managed.exitCode = exitCode;

    // Show exit message in terminal
    const codeStr = exitCode !== null ? String(exitCode) : "unknown";
    managed.xterm.writeln(`\r\n\x1b[2m[Process exited with code ${codeStr}]\x1b[0m`);

    // Show dead overlay
    const overlay = managed.container.querySelector(".dead-overlay");
    if (overlay) {
        overlay.classList.remove("hidden");
        const text = overlay.querySelector(".dead-text");
        if (text) {
            text.textContent = `[exited: ${codeStr}]`;
        }
    }

    // Update status indicator
    const indicator = managed.container.querySelector(".pane-status-indicator");
    if (indicator) {
        indicator.className = "pane-status-indicator pane-status-dead";
    }

    updateTerminalCount();
};

/**
 * Handle window resize — refit all terminal instances.
 */
function refitAllTerminals() {
    for (const managed of terminals.values()) {
        try {
            managed.fitAddon.fit();
        } catch {
            // Terminal may be disposed
        }
    }
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
 * Terminal grid is managed separately via xterm.js — only sidebar
 * panels and status bar are rendered from snapshot data.
 * @param {object} snap
 */
function renderAll(snap) {
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


/* ── Focused Pane Management ──────────────────────────────── */

/**
 * Set focused pane by ID and update visual state.
 * Focuses the xterm instance so keyboard input goes to it.
 * @param {string} paneId
 */
function setFocusedPane(paneId) {
    focusedPaneId = paneId;
    for (const el of document.querySelectorAll(".terminal-pane")) {
        el.classList.toggle("focused", el.dataset.paneId === paneId);
    }
    // Focus the xterm instance
    const managed = terminals.get(paneId);
    if (managed) {
        managed.xterm.focus();
    }
}

/**
 * Focus a pane by its user number (1-9).
 * @param {number} number
 */
function focusPaneByNumber(number) {
    const paneId = numberToPaneId.get(number);
    if (paneId) {
        setFocusedPane(paneId);
    }
}

/**
 * Unfocus the current terminal so global shortcuts work.
 */
function unfocusTerminal() {
    focusedPaneId = null;
    for (const el of document.querySelectorAll(".terminal-pane")) {
        el.classList.remove("focused");
    }
    // Blur any focused xterm
    for (const managed of terminals.values()) {
        managed.xterm.blur();
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
 * Update the terminal count in the status bar.
 */
function updateTerminalCount() {
    const alive = Array.from(terminals.values()).filter(t => !t.dead).length;
    const total = terminals.size;
    const label = total === 0
        ? "0 terminals"
        : alive === total
            ? `${total} terminal${total !== 1 ? "s" : ""}`
            : `${alive}/${total} terminals`;

    updateStatusItem("status-instances", label,
        alive > 0 ? "dot-green" : "dot-dim");
}

/**
 * Render the status bar with counts and health info.
 * @param {object} snap
 */
function renderStatusBar(snap) {
    const eventCount = snap.recent_events ? snap.recent_events.length : 0;
    const planCount = snap.soul_state && snap.soul_state.plan ? snap.soul_state.plan.length : 0;
    const blockerCount = snap.soul_state && snap.soul_state.blockers ? snap.soul_state.blockers.length : 0;

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
        // When a terminal is focused, only intercept global shortcuts with modifiers
        const terminalFocused = focusedPaneId !== null && document.activeElement &&
            document.activeElement.closest(".xterm");

        if (terminalFocused) {
            // Escape unfocuses the terminal so other shortcuts work
            if (e.key === "Escape") {
                e.preventDefault();
                unfocusTerminal();
                return;
            }
            // Let all other keys pass through to xterm when terminal is focused
            return;
        }

        // Don't intercept when typing in an input
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

        const key = e.key.toLowerCase();

        switch (key) {
            case "n":
                e.preventDefault();
                spawnTerminal();
                break;
            case "w":
                e.preventDefault();
                if (focusedPaneId) killTerminal(focusedPaneId);
                break;
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
    } catch {
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

    // Handle window resize — refit all terminals (debounced to avoid
    // flooding the backend with resize_terminal calls during drag)
    let resizeTimer = null;
    function debouncedRefit() {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(refitAllTerminals, 100);
    }

    window.addEventListener("resize", debouncedRefit);

    // Observe grid layout changes for terminal refitting
    const grid = document.getElementById("terminal-grid");
    if (grid) {
        const resizeObserver = new ResizeObserver(debouncedRefit);
        resizeObserver.observe(grid);
    }

    // New terminal button
    const newTermBtn = document.getElementById("new-terminal-btn");
    if (newTermBtn) {
        newTermBtn.addEventListener("click", () => spawnTerminal());
    }
});
