// CloudLens RCA Platform — Advanced Autonomous Controller
// Supports Light/Dark Mode, Visual Blast Radius, Hover Diagnostics, SRE Voice Briefing,
// Custom Fault Injection, Zero-Event Clean State, Timeline Filtering & Auto-Refresh.

// Universal API_BASE: routes to port 8000 from VS Code Live Server, Vite, static files, or direct browser
const API_BASE = (window.location.port === "8000" && window.location.protocol !== "file:") 
    ? "/api" 
    : "http://127.0.0.1:8000/api";

// Application State
let currentIncident = null;
let currentIncidentDetail = null;
let currentTopology = null;
let allEvents = [];
let availableScenarios = [];
let activeScenarioId = "cascade";
let selectedNodeId = null;

// Real-Time Auto Sync
let autoSyncTimer = null;
let isAutoSyncEnabled = false;

window.showApiError = function(msg = "Cannot connect to backend server. Make sure it's running!") {
    const toast = document.getElementById("apiErrorToast");
    const toastText = document.getElementById("apiErrorText");
    if (toast && toastText) {
        toastText.textContent = msg;
        toast.style.display = "flex";
        setTimeout(() => { toast.style.display = "none"; }, 5000);
    }
    console.error("API Error:", msg);
};

// Speech Synthesis State
let isSpeaking = false;

// Node Positions for Clean Topology Rendering
const NODE_POSITIONS = {
    "EC2": { x: 110, y: 80, icon: "🖥️", title: "EC2 Host", desc: "Compute Instance" },
    "DynamoDB": { x: 110, y: 200, icon: "🗄️", title: "PostgreSQL / DB", desc: "Database Tier" },
    "Auth": { x: 110, y: 320, icon: "🔐", title: "Auth Service", desc: "OAuth2 IdP" },
    "Worker": { x: 310, y: 290, icon: "⚙️", title: "Worker Pods", desc: "Background Queue" },
    "API": { x: 310, y: 140, icon: "🌐", title: "API Gateway", desc: "Core Backend" },
    "Application": { x: 510, y: 200, icon: "💻", title: "Web App UI", desc: "Frontend Client" }
};

const SCENARIO_ICONS = {
    "cascade": "🖥️",
    "db_exhaustion": "🗄️",
    "auth_outage": "🔐",
    "memory_leak": "⚙️"
};

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
}

// DOM Elements
const awsStatusPill = document.getElementById("awsStatusPill");
const btnPollAws = document.getElementById("btnPollAws");
const btnReset = document.getElementById("btnReset");
const btnCopyCommand = document.getElementById("btnCopyCommand");
const btnApplyFix = document.getElementById("btnApplyFix");
const fixStatusMsg = document.getElementById("fixStatusMsg");
const fixActionTypeBadge = document.getElementById("fixActionTypeBadge");
const fixActionSubtitle = document.getElementById("fixActionSubtitle");
const fixEstimatedTime = document.getElementById("fixEstimatedTime");
const safetyChecksBox = document.getElementById("safetyChecksBox");
const safetyChecksList = document.getElementById("safetyChecksList");

const scenarioSelector = document.getElementById("scenarioSelector");
const btnOpenFaultModal = document.getElementById("btnOpenFaultModal");
const btnCleanState = document.getElementById("btnCleanState");

const incidentTitle = document.getElementById("incidentTitle");
const incidentSubtitle = document.getElementById("incidentSubtitle");
const incidentResourceId = document.getElementById("incidentResourceId");
const incidentRegion = document.getElementById("incidentRegion");
const incidentStatusTag = document.getElementById("incidentStatusTag");
const incidentCompoundBadge = document.getElementById("incidentCompoundBadge");

const kpiEventCount = document.getElementById("kpiEventCount");
const kpiIncidentCount = document.getElementById("kpiIncidentCount");
const kpiRootCause = document.getElementById("kpiRootCause");
const kpiConfidenceBadge = document.getElementById("kpiConfidenceBadge");
const kpiBlastCount = document.getElementById("kpiBlastCount");

const topologySvg = document.getElementById("topologySvg");
const selectedServiceCard = document.getElementById("selectedServiceCard");
const inspectorRiskTag = document.getElementById("inspectorRiskTag");
const inspectorContent = document.getElementById("inspectorContent");
const graphNodeTooltip = document.getElementById("graphNodeTooltip");

const diagnosisCauseTitle = document.getElementById("diagnosisCauseTitle");
const diagnosisResourceId = document.getElementById("diagnosisResourceId");
const diagnosisConfidenceTag = document.getElementById("diagnosisConfidenceTag");
const diagnosisExplanation = document.getElementById("diagnosisExplanation");
const diagnosisSeverityTag = document.getElementById("diagnosisSeverityTag");
const causalPathsSection = document.getElementById("causalPathsSection");
const causalPathsList = document.getElementById("causalPathsList");
const scoringBars = document.getElementById("scoringBars");
const candidateRankingSection = document.getElementById("candidateRankingSection");
const candidateRankingBody = document.getElementById("candidateRankingBody");
const reasoningList = document.getElementById("reasoningList");

const remediationCommand = document.getElementById("remediationCommand");

const timelineList = document.getElementById("timelineList");
const timelineEventCount = document.getElementById("timelineEventCount");
const timelineSearchInput = document.getElementById("timelineSearchInput");
const timelineSeverityFilter = document.getElementById("timelineSeverityFilter");
const timelineServiceFilter = document.getElementById("timelineServiceFilter");
const btnToggleAutoSync = document.getElementById("btnToggleAutoSync");
const syncDot = document.getElementById("syncDot");
const autoSyncLabel = document.getElementById("autoSyncLabel");

const step1 = document.getElementById("step1");
const step2 = document.getElementById("step2");
const step3 = document.getElementById("step3");

const btnThemeToggle = document.getElementById("btnThemeToggle");
const themeIcon = document.getElementById("themeIcon");
const themeLabel = document.getElementById("themeLabel");

const onCallBadge = document.getElementById("onCallBadge");
const onCallTeamName = document.getElementById("onCallTeamName");

const faultInjectorModal = document.getElementById("faultInjectorModal");
const btnCloseFaultModal = document.getElementById("btnCloseFaultModal");
const btnCancelFaultModal = document.getElementById("btnCancelFaultModal");

// ---------------------------------------------------------------------------
// 1. Initialization
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initOnCall();
    setupEventListeners();
    // Auto-sync is off by default now based on user request.
    initAutoSync();

    // Fast parallel loading: fetch AWS status, scenarios, and dashboard data simultaneously
    Promise.allSettled([
        fetchAwsStatus().catch(() => showApiError()),
        fetchCloudArchitectureStatus().catch(() => {}),
        fetchScenarios().catch(() => showApiError()),
        fetchDashboardData().catch(() => {})
    ]);

    // Connect to real-time WebSocket stream
    initWebSocket();
});

function setupEventListeners() {
    // Top Actions
    btnPollAws.addEventListener("click", () => pollLiveAws(true));
    btnReset.addEventListener("click", resetSystem);
    btnApplyFix.addEventListener("click", applyRemediationFix);

    // Amazon S3 & SNS Integration Buttons (Review 1)
    const btnDiscover = document.getElementById("btnDiscoverAws");
    if (btnDiscover) btnDiscover.addEventListener("click", discoverAwsAssets);

    const btnViewS3 = document.getElementById("btnViewS3PostMortem");
    if (btnViewS3) btnViewS3.addEventListener("click", openS3PostMortemModal);

    const btnTestSns = document.getElementById("btnTestSnsAlert");
    if (btnTestSns) btnTestSns.addEventListener("click", triggerTestSnsAlert);

    const btnCloseS3 = document.getElementById("btnCloseS3Modal");
    if (btnCloseS3) btnCloseS3.addEventListener("click", closeS3PostMortemModal);

    const btnCloseS3B = document.getElementById("btnCloseS3ModalBtn");
    if (btnCloseS3B) btnCloseS3B.addEventListener("click", closeS3PostMortemModal);

    const btnCopyS3 = document.getElementById("btnCopyS3Json");
    if (btnCopyS3) btnCopyS3.addEventListener("click", copyS3ModalContent);

    const s3Modal = document.getElementById("s3PostMortemModal");
    if (s3Modal) {
        s3Modal.addEventListener("click", (e) => {
            if (e.target === s3Modal) closeS3PostMortemModal();
        });
    }

    // Theme Toggle
    if (btnThemeToggle) btnThemeToggle.addEventListener("click", toggleTheme);

    // On-Call Customizer
    if (onCallBadge) onCallBadge.addEventListener("click", customizeOnCall);

    // Scenario & Fault Ingestion
    if (btnOpenFaultModal) btnOpenFaultModal.addEventListener("click", openFaultModal);
    if (btnCloseFaultModal) btnCloseFaultModal.addEventListener("click", closeFaultModal);
    if (btnCancelFaultModal) btnCancelFaultModal.addEventListener("click", closeFaultModal);
    if (btnCleanState) btnCleanState.addEventListener("click", setCleanZeroState);

    if (faultInjectorModal) {
        faultInjectorModal.addEventListener("click", (e) => {
            if (e.target === faultInjectorModal) closeFaultModal();
        });
    }

    // Copy Runbook
    if (btnCopyCommand) {
        btnCopyCommand.addEventListener("click", () => {
            navigator.clipboard.writeText(remediationCommand.innerText).then(() => {
                btnCopyCommand.textContent = "Copied!";
                setTimeout(() => { btnCopyCommand.textContent = "Copy"; }, 2000);
            });
        });
    }

    // Timeline Filtering
    if (timelineSearchInput) timelineSearchInput.addEventListener("input", filterAndRenderTimeline);
    if (timelineSeverityFilter) timelineSeverityFilter.addEventListener("change", filterAndRenderTimeline);
    if (timelineServiceFilter) timelineServiceFilter.addEventListener("change", filterAndRenderTimeline);
    if (btnToggleAutoSync) btnToggleAutoSync.addEventListener("click", toggleAutoSync);

    // Click canvas background to deselect node highlight
    if (topologySvg) {
        topologySvg.addEventListener("click", (e) => {
            if (e.target.tagName === "svg" || e.target.classList.contains("topology-svg")) {
                clearGraphBlastHighlight();
            }
        });
    }
}

// ---------------------------------------------------------------------------
// 2. Theme Engine (Light / Dark Mode)
// ---------------------------------------------------------------------------
function initTheme() {
    const savedTheme = localStorage.getItem("cloudlens_theme");
    if (savedTheme) {
        setTheme(savedTheme);
    } else {
        const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        setTheme(prefersDark ? "dark" : "light");
    }
}

function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("cloudlens_theme", theme);

    if (themeIcon && themeLabel) {
        if (theme === "dark") {
            themeIcon.textContent = "☀️";
            themeLabel.textContent = "Light";
        } else {
            themeIcon.textContent = "🌙";
            themeLabel.textContent = "Dark";
        }
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    setTheme(current === "dark" ? "light" : "dark");
}

// ---------------------------------------------------------------------------
// 3. On-Call Customizer
// ---------------------------------------------------------------------------
function initOnCall() {
    const saved = localStorage.getItem("cloudlens_oncall");
    if (saved && onCallTeamName) {
        onCallTeamName.textContent = saved;
    }
}

function customizeOnCall() {
    const current = onCallTeamName.textContent || "SRE Team";
    const updated = prompt("Enter SRE On-Call Responder / Team Name:", current);
    if (updated && updated.trim()) {
        onCallTeamName.textContent = updated.trim();
        localStorage.setItem("cloudlens_oncall", updated.trim());
    }
}

// ---------------------------------------------------------------------------
// 5. AWS Connection Status
// ---------------------------------------------------------------------------
async function fetchAwsStatus() {
    try {
        const res = await fetch(`${API_BASE}/aws/status`);
        const data = await res.json();
        if (data.connected) {
            awsStatusPill.className = "status-pill status-pill-success";
            awsStatusPill.innerHTML = `<span class="status-dot"></span><span>AWS Connected (${escapeHtml(data.region || "us-east-1")})</span>`;
        } else {
            awsStatusPill.className = "status-pill";
            awsStatusPill.style.background = "#FEF2F2";
            awsStatusPill.style.color = "#DC2626";
            awsStatusPill.innerHTML = `<span class="status-dot" style="background:#DC2626;"></span><span>AWS Disconnected</span>`;
        }
    } catch (e) {
        console.warn("AWS Status notice:", e);
    }
}

// ---------------------------------------------------------------------------
// 6. Scenario Management & Custom Fault Injection
// ---------------------------------------------------------------------------
async function fetchScenarios() {
    try {
        const res = await fetch(`${API_BASE}/scenarios`);
        if (!res.ok) return;
        availableScenarios = await res.json();
        renderScenarioButtons();
    } catch (err) {
        console.error("Failed to load scenarios:", err);
    }
}

function renderScenarioButtons() {
    if (!scenarioSelector || !availableScenarios.length) return;

    scenarioSelector.innerHTML = availableScenarios.map(s => {
        const isActive = s.id === activeScenarioId;
        const icon = SCENARIO_ICONS[s.id] || "⚡";
        const shortTitle = s.name.includes("&") ? s.name.split("&")[0].trim() : s.name;

        return `
            <button class="scenario-btn ${isActive ? 'is-active' : ''}" onclick="switchScenario('${escapeHtml(s.id)}')">
                <div class="scenario-btn-top">
                    <div class="scenario-icon-title">
                        <span>${icon}</span>
                        <span>${escapeHtml(shortTitle)}</span>
                    </div>
                    <span class="scenario-category-pill">${escapeHtml(s.category || 'Outage')}</span>
                </div>
                <div class="scenario-desc-text" title="${escapeHtml(s.description)}">${escapeHtml(s.description)}</div>
            </button>
        `;
    }).join("");
}

window.switchScenario = async function(scenarioId) {
    activeScenarioId = scenarioId;
    renderScenarioButtons();

    if (fixStatusMsg) fixStatusMsg.textContent = "";
    clearGraphBlastHighlight();

    try {
        const res = await fetch(`${API_BASE}/scenarios/${scenarioId}/simulate`, { method: "POST" });
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        await fetchDashboardData();
    } catch (e) {
        console.error("Scenario switch error:", e);
        showApiError("Failed to trigger scenario: " + e.message);
    }
};

// Zero-Event Clean State (Traces back & clears fabricated events)
async function setCleanZeroState() {
    activeScenarioId = null;
    renderScenarioButtons();
    clearGraphBlastHighlight();
    if (fixStatusMsg) fixStatusMsg.textContent = "";

    try {
        await fetch(`${API_BASE}/reset`, { method: "POST" });
        await fetchDashboardData();
    } catch (e) {
        console.error("Clean state error:", e);
        showApiError("Failed to reset state: " + e.message);
    }
}

// Modal: Custom Fault Injector
function openFaultModal() {
    if (faultInjectorModal) faultInjectorModal.style.display = "flex";
}

function closeFaultModal() {
    if (faultInjectorModal) faultInjectorModal.style.display = "none";
}

window.submitCustomFault = async function() {
    const service = document.getElementById("faultTargetService").value;
    const severity = document.getElementById("faultSeverity").value;
    const eventType = document.getElementById("faultEventType").value;
    const source = document.getElementById("faultSource").value;
    const metricName = document.getElementById("faultMetricName").value;
    const metricVal = parseFloat(document.getElementById("faultMetricValue").value) || 99.0;
    const resourceId = document.getElementById("faultResourceId").value;
    const region = document.getElementById("faultRegion").value;

    const customEvent = {
        event_id: "evt_" + Math.random().toString(36).substring(2, 9),
        timestamp: new Date().toISOString(),
        source: source,
        event_type: eventType,
        service: service,
        resource_id: resourceId,
        metric: metricName,
        value: metricVal,
        severity: severity,
        metadata: { region: region, injected: "custom_fault" }
    };

    try {
        const res = await fetch(`${API_BASE}/events`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(customEvent)
        });
        if (!res.ok) throw new Error("Fault ingestion failed");

        closeFaultModal();
        activeScenarioId = null;
        renderScenarioButtons();
        clearGraphBlastHighlight();
        await fetchDashboardData();
    } catch (err) {
        alert(`Failed to fire telemetry: ${err.message}`);
    }
};

// ---------------------------------------------------------------------------
// 7. Poll AWS Alarms
// ---------------------------------------------------------------------------
async function pollLiveAws(isManual = false) {
    if (isManual) {
        btnPollAws.disabled = true;
        btnPollAws.textContent = "Checking...";
        activeScenarioId = null;
        renderScenarioButtons();
    }

    try {
        const res = await fetch(`${API_BASE}/aws/poll`, { method: "POST" });
        const data = await res.json();
        await fetchDashboardData();
    } catch (e) {
        console.error("Poll error:", e);
        if (isManual) showApiError("Failed to connect to backend for sync.");
    } finally {
        if (isManual) {
            btnPollAws.disabled = false;
            btnPollAws.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg> Connect / Sync Data`;
        }
    }
}

// ---------------------------------------------------------------------------
// 8. Real-Time Auto-Refresh
// ---------------------------------------------------------------------------
function initAutoSync() {
    if (isAutoSyncEnabled) {
        startAutoSync();
    }
}

function startAutoSync() {
    if (autoSyncTimer) clearInterval(autoSyncTimer);
    autoSyncTimer = setInterval(() => {
        fetchDashboardData(true);
    }, 5000);
}

function stopAutoSync() {
    if (autoSyncTimer) {
        clearInterval(autoSyncTimer);
        autoSyncTimer = null;
    }
}

function toggleAutoSync() {
    isAutoSyncEnabled = !isAutoSyncEnabled;
    if (isAutoSyncEnabled) {
        startAutoSync();
        btnToggleAutoSync.classList.add("is-active");
        syncDot.className = "sync-dot live";
        autoSyncLabel.textContent = "Auto-Sync: ON (5s)";
    } else {
        stopAutoSync();
        btnToggleAutoSync.classList.remove("is-active");
        syncDot.className = "sync-dot";
        autoSyncLabel.textContent = "Auto-Sync: OFF";
    }
}

// ---------------------------------------------------------------------------
// 9. Fetch Dashboard Data
// ---------------------------------------------------------------------------
async function fetchDashboardData(isBackground = false) {
    try {
        const [topRes, incRes, evtRes] = await Promise.all([
            fetch(`${API_BASE}/topology`),
            fetch(`${API_BASE}/incidents`),
            fetch(`${API_BASE}/events`)
        ]);

        currentTopology = await topRes.json();
        const incidents = await incRes.json();
        allEvents = await evtRes.json();

        kpiEventCount.textContent = allEvents.length;
        kpiIncidentCount.textContent = incidents.length;

        if (incidents.length > 0) {
            currentIncident = incidents[0];
            await fetchIncidentDetails(currentIncident.incident_id);
        } else {
            currentIncident = null;
            currentIncidentDetail = null;
            renderEmptyState();
        }

        renderTopology(currentTopology, currentIncidentDetail);
        filterAndRenderTimeline();

    } catch (err) {
        console.error("Dashboard fetch error:", err);
    }
}

// ---------------------------------------------------------------------------
// 10. Fetch Incident RCA Details (GET /api/incidents/{id})
// ---------------------------------------------------------------------------
async function fetchIncidentDetails(incidentId) {
    try {
        const res = await fetch(`${API_BASE}/incidents/${incidentId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        currentIncidentDetail = await res.json();
        renderDiagnosis(currentIncidentDetail);
    } catch (err) {
        console.error("Incident analysis fetch error:", err);
    }
}

// ---------------------------------------------------------------------------
// 11. Render SVG Topology with Visual Blast Radius & Hover Tooltip
// ---------------------------------------------------------------------------
function renderTopology(topology, incidentDetail) {
    if (!topology || !topology.nodes) return;

    const rootCause = incidentDetail?.probable_root_cause || null;
    const affected = incidentDetail?.affected_services || [];

    // 1. Build dynamic node coordinate map from topology.nodes
    const nodeMap = {};
    (topology.nodes || []).forEach(node => {
        nodeMap[node.id] = {
            x: node.x || NODE_POSITIONS[node.id]?.x || 250,
            y: node.y || NODE_POSITIONS[node.id]?.y || 150,
            icon: node.icon || NODE_POSITIONS[node.id]?.icon || "🌐",
            title: node.label || node.id,
            desc: node.desc || `${node.type || 'service'} tier`
        };
    });

    let svgHtml = `
        <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="26" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#94A3B8" />
            </marker>
            <marker id="arrow-root" viewBox="0 0 10 10" refX="26" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 1 L 9 5 L 0 9 z" fill="#EF4444" />
            </marker>
            <marker id="arrow-blast" viewBox="0 0 10 10" refX="26" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 1 L 9 5 L 0 9 z" fill="#F59E0B" />
            </marker>
        </defs>
    `;

    // 2. Draw Edges
    (topology.edges || []).forEach(edge => {
        const src = nodeMap[edge.source];
        const tgt = nodeMap[edge.target];
        if (!src || !tgt) return;

        const isCausal = rootCause && (edge.source === rootCause || edge.target === rootCause);
        const edgeClass = isCausal ? "edge-path edge-path-root" : "edge-path edge-path-animated";
        const marker = isCausal ? "url(#arrow-root)" : "url(#arrow)";

        const dx = tgt.x - src.x;
        const dy = tgt.y - src.y;
        const cx1 = src.x + dx * 0.4;
        const cy1 = src.y;
        const cx2 = src.x + dx * 0.6;
        const cy2 = tgt.y;

        svgHtml += `<path id="edge_${escapeHtml(edge.source)}_${escapeHtml(edge.target)}" d="M ${src.x} ${src.y} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${tgt.x} ${tgt.y}" class="${edgeClass}" marker-end="${marker}" />`;
    });

    // 3. Draw Nodes Dynamically
    topology.nodes.forEach(node => {
        const pos = nodeMap[node.id] || { x: 250, y: 150, icon: "📦", title: node.id, desc: "Service" };
        const isRoot = rootCause === node.id;
        const isAff = affected.includes(node.id);

        let nodeClass = "node-card node-healthy";
        let tagText = "HEALTHY";
        let tagColor = "#059669";
        let tagBg = "#ECFDF5";

        if (isRoot) {
            nodeClass = "node-card node-root-cause";
            tagText = "ROOT CAUSE";
            tagColor = "#DC2626";
            tagBg = "#FEE2E2";
        } else if (isAff) {
            nodeClass = "node-card node-degraded";
            tagText = "DEGRADED";
            tagColor = "#D97706";
            tagBg = "#FEF3C7";
        }

        const width = 135;
        const height = 54;
        const rx = pos.x - width / 2;
        const ry = pos.y - height / 2;

        svgHtml += `
            <g id="nodeGroup_${escapeHtml(node.id)}" class="${nodeClass}" 
               onclick="inspectServiceNode('${escapeHtml(node.id)}')"
               onmouseenter="handleNodeHover(event, '${escapeHtml(node.id)}')"
               onmouseleave="handleNodeLeave()">
                <rect x="${rx}" y="${ry}" width="${width}" height="${height}" class="node-bg" />
                <text x="${rx + 14}" y="${ry + 28}" font-size="16">${pos.icon}</text>
                <text x="${rx + 36}" y="${ry + 22}" class="node-text-title">${escapeHtml(pos.title)}</text>
                <text x="${rx + 36}" y="${ry + 34}" class="node-text-desc">${escapeHtml(pos.desc)}</text>
                <rect x="${rx + 36}" y="${ry + 39}" width="${tagText.length * 5.5 + 8}" height="11" rx="3" fill="${tagBg}" />
                <text x="${rx + 40}" y="${ry + 48}" class="node-text-tag" fill="${tagColor}">${tagText}</text>
            </g>
        `;
    });

    topologySvg.innerHTML = svgHtml;
}

// ---------------------------------------------------------------------------
// 12. Visual Blast Radius Highlighting on Node Click
// ---------------------------------------------------------------------------
window.inspectServiceNode = async function(serviceId) {
    if (selectedNodeId === serviceId) {
        clearGraphBlastHighlight();
        return;
    }

    selectedNodeId = serviceId;

    try {
        const res = await fetch(`${API_BASE}/what-if/${encodeURIComponent(serviceId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        const direct = data.direct_downstream_services || [];
        const transitive = data.all_impacted_services || [];
        const risk = Math.round(data.estimated_architecture_risk_score ?? 0);
        const crit = data.criticality_level || "MEDIUM";
        const rec = data.remediation_recommendation || "";

        // Highlight directly on SVG topology
        applyVisualBlastHighlight(serviceId, direct, transitive);

        // Update Inspector Card
        let riskColor = "#059669";
        let riskBg = "#ECFDF5";
        let riskBorder = "#A7F3D0";
        if (risk >= 70) {
            riskColor = "#DC2626";
            riskBg = "#FEE2E2";
            riskBorder = "#FECACA";
        } else if (risk >= 40) {
            riskColor = "#D97706";
            riskBg = "#FEF3C7";
            riskBorder = "#FDE68A";
        }

        inspectorRiskTag.textContent = `Risk Score: ${risk}/100`;
        inspectorRiskTag.style.color = riskColor;
        inspectorRiskTag.style.background = riskBg;
        inspectorRiskTag.style.borderColor = riskBorder;

        inspectorContent.innerHTML = `
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem;">
                <span style="font-size: 0.88rem; font-weight: 700; color: var(--text-heading);">Inspected: ${escapeHtml(serviceId)}</span>
                <span style="font-size: 0.68rem; font-weight: 700; padding: 0.15rem 0.5rem; border-radius: 4px; background: ${crit === 'HIGH' ? '#FEE2E2' : '#FEF3C7'}; color: ${crit === 'HIGH' ? '#DC2626' : '#D97706'};">
                    CRITICALITY: ${escapeHtml(crit)}
                </span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 0.35rem; font-size: 0.78rem; line-height: 1.45;">
                <div>• <strong>Direct Callers (${direct.length}):</strong> ${direct.length ? escapeHtml(direct.join(", ")) : '<span style="color:var(--text-muted); font-style:italic;">None (Edge Service)</span>'}</div>
                <div>• <strong>Blast Radius (${transitive.length}):</strong> <span style="color:#DC2626; font-weight:600;">${transitive.length ? escapeHtml(transitive.join(" ➔ ")) : '<span style="color:var(--emerald); font-style:italic;">Isolated to this node</span>'}</span></div>
                ${rec ? `<div style="margin-top:0.25rem; padding-top:0.35rem; border-top: 1px dashed var(--border); color: var(--text-muted);">• <strong>Architectural Guidance:</strong> ${escapeHtml(rec)}</div>` : ''}
            </div>
            <div style="margin-top: 0.5rem; text-align: right;">
                <button class="btn btn-ghost btn-sm" onclick="clearGraphBlastHighlight()" style="font-size:0.7rem; padding:0.15rem 0.5rem;">Clear Highlight</button>
            </div>
        `;
    } catch (e) {
        console.error("Inspector error:", e);
    }
};

function applyVisualBlastHighlight(targetId, directNodes, transitiveNodes) {
    const allImpacted = new Set(transitiveNodes);

    Object.keys(NODE_POSITIONS).forEach(nodeId => {
        const el = document.getElementById(`nodeGroup_${nodeId}`);
        if (!el) return;

        // Reset blast classes
        el.classList.remove("node-selected", "node-blast-direct", "node-blast-transitive", "node-dimmed");

        if (nodeId === targetId) {
            el.classList.add("node-selected");
        } else if (directNodes.includes(nodeId)) {
            el.classList.add("node-blast-direct");
        } else if (allImpacted.has(nodeId)) {
            el.classList.add("node-blast-transitive");
        } else {
            el.classList.add("node-dimmed");
        }
    });

    // Highlight connecting blast edges
    if (currentTopology?.edges) {
        currentTopology.edges.forEach(edge => {
            const edgeEl = document.getElementById(`edge_${edge.source}_${edge.target}`);
            if (!edgeEl) return;

            const isBlastEdge = (edge.source === targetId) || 
                                (directNodes.includes(edge.source) && allImpacted.has(edge.target));

            if (isBlastEdge) {
                edgeEl.classList.add("edge-path-blast");
                edgeEl.setAttribute("marker-end", "url(#arrow-blast)");
            } else {
                edgeEl.classList.remove("edge-path-blast");
            }
        });
    }
}

window.clearGraphBlastHighlight = function() {
    selectedNodeId = null;

    Object.keys(NODE_POSITIONS).forEach(nodeId => {
        const el = document.getElementById(`nodeGroup_${nodeId}`);
        if (el) {
            el.classList.remove("node-selected", "node-blast-direct", "node-blast-transitive", "node-dimmed");
        }
    });

    if (currentTopology?.edges) {
        currentTopology.edges.forEach(edge => {
            const edgeEl = document.getElementById(`edge_${edge.source}_${edge.target}`);
            if (edgeEl) {
                edgeEl.classList.remove("edge-path-blast");
            }
        });
    }

    if (inspectorRiskTag) inspectorRiskTag.textContent = "Click a node to inspect";
    if (inspectorContent) {
        inspectorContent.innerHTML = "Click any service node in the topology above to simulate hypothetical outages, evaluate transitive blast radius, and view upstream callers.";
    }
};

// ---------------------------------------------------------------------------
// 13. Component Hover Diagnostics Tooltip Popover
// ---------------------------------------------------------------------------
window.handleNodeHover = function(e, nodeId) {
    if (!graphNodeTooltip) return;

    const pos = NODE_POSITIONS[nodeId] || { icon: "📦", title: nodeId };
    const rootCause = currentIncidentDetail?.probable_root_cause;
    const affected = currentIncidentDetail?.affected_services || [];

    const isRoot = rootCause === nodeId;
    const isAff = affected.includes(nodeId);

    let tagText = "HEALTHY";
    let tagClass = "candidate-badge-pill candidate-badge-symptom";
    let reasonText = "Operating within nominal performance thresholds.";

    if (isRoot) {
        tagText = "PRIMARY ROOT";
        tagClass = "candidate-badge-pill candidate-badge-root";
        reasonText = currentIncidentDetail?.reasoning_chain?.[0] || 
                     `Triggered earliest threshold breach, starves downstream dependents.`;
    } else if (isAff) {
        tagText = "DEGRADED";
        tagClass = "candidate-badge-pill candidate-badge-secondary";
        reasonText = `Cascading degradation propagated from primary root cause '${rootCause}'.`;
    }

    // Telemetry metric info
    const relatedEvent = (allEvents || []).find(ev => ev.service === nodeId);
    const metricText = relatedEvent ? `${relatedEvent.metric || relatedEvent.event_type}: ${relatedEvent.value ?? 'anomalous'}` : "Nominal";

    document.getElementById("ttIcon").textContent = pos.icon;
    document.getElementById("ttTitle").textContent = pos.title;
    const ttTag = document.getElementById("ttTag");
    ttTag.textContent = tagText;
    ttTag.className = `tooltip-tag ${tagClass}`;

    document.getElementById("ttReason").textContent = reasonText;
    document.getElementById("ttMetric").textContent = metricText;
    document.getElementById("ttDependents").textContent = `${(currentIncidentDetail?.affected_services?.length || 0)} affected`;

    // Position tooltip
    const svgWrap = document.querySelector(".graph-canvas-wrap");
    if (svgWrap) {
        const wrapRect = svgWrap.getBoundingClientRect();
        const clientX = e.clientX;
        const clientY = e.clientY;

        let left = clientX - wrapRect.left + 15;
        let top = clientY - wrapRect.top + 10;

        if (left + 280 > wrapRect.width) left = wrapRect.width - 285;
        if (top + 140 > wrapRect.height) top = wrapRect.height - 145;

        graphNodeTooltip.style.left = `${Math.max(10, left)}px`;
        graphNodeTooltip.style.top = `${Math.max(10, top)}px`;
        graphNodeTooltip.style.display = "block";
    }
};

window.handleNodeLeave = function() {
    if (graphNodeTooltip) graphNodeTooltip.style.display = "none";
};

// ---------------------------------------------------------------------------
// 14. Render Diagnosis Details
// ---------------------------------------------------------------------------
function renderDiagnosis(detail) {
    if (!detail) return;

    const cause = detail.probable_root_cause || "EC2";
    const confidence = Math.round(detail.confidence ?? 0);
    const rootEvent = (currentIncident?.events || []).find(e => e.service === cause) || currentIncident?.events?.[0];
    const resource = rootEvent?.resource_id || detail.resource_id || "target-resource";
    const region = rootEvent?.metadata?.aws_region || rootEvent?.metadata?.region || "eu-north-1";

    // KPIs
    kpiRootCause.textContent = cause;
    kpiRootCause.className = "kpi-big-num text-danger";
    kpiConfidenceBadge.textContent = `${confidence}% confidence`;
    kpiBlastCount.textContent = (detail.affected_services?.length || 0) + 1;

    // Headline & Region
    const activeScenario = availableScenarios.find(s => s.id === activeScenarioId);
    if (activeScenario) {
        incidentTitle.textContent = activeScenario.name;
    } else {
        const isLiveAws = (currentIncident?.events || []).some(e => e.source === "cloudwatch" || e.source === "cloudtrail");
        incidentTitle.textContent = isLiveAws
            ? `Live AWS Telemetry: ${cause} Outage Detected`
            : `${cause} Outage & Cascading Failure`;
    }

    incidentResourceId.textContent = resource;
    if (incidentRegion) incidentRegion.textContent = region;

    // Status & Stepper
    const isResolved = currentIncident?.status === "RESOLVED";
    if (isResolved) {
        incidentStatusTag.textContent = "RESOLVED";
        incidentStatusTag.className = "status-tag status-tag-resolved";
        updateLifecycleStepper("RESOLVED");
    } else {
        incidentStatusTag.textContent = "OPEN ANOMALY";
        incidentStatusTag.className = "status-tag status-tag-open";
        updateLifecycleStepper("OPEN");
    }

    // Compound Incident Indicator
    if (detail.is_compound_incident) {
        incidentCompoundBadge.style.display = "inline-flex";
        const secondary = (detail.contributing_causes || []).join(", ") || "Secondary Root";
        incidentCompoundBadge.textContent = `⚠️ COMPOUND INCIDENT (${secondary})`;
    } else {
        incidentCompoundBadge.style.display = "none";
    }

    // Diagnosis Hero Card
    diagnosisCauseTitle.textContent = `${cause} Outage Detected`;
    diagnosisResourceId.textContent = resource;
    diagnosisConfidenceTag.textContent = `${confidence}% CONFIDENCE`;
    diagnosisSeverityTag.textContent = `${detail.severity || 'HIGH'} SEVERITY`;
    diagnosisExplanation.textContent = detail.explanation || detail.remediation_suggestion || (detail.reasoning_chain?.[0] ?? "Anomaly detected in telemetry stream.");

    // Causal Paths
    renderCausalPaths(detail.causal_paths, cause);

    // 5 Scoring Dimensions
    renderScoringBars(detail.score_breakdown);

    // Candidate Ranking
    renderCandidateRanking(detail.all_candidate_scores, cause, detail.contributing_causes);

    // Reasoning Chain
    const reasoning = detail.reasoning_chain || [
        `Service '${cause}' registered the earliest anomaly in the incident timeline.`,
        `'${cause}' occupies a structural dependency position affecting downstream services.`,
        `Calibrated Result: '${cause}' determined as Primary Root Cause with ${confidence}% confidence.`
    ];

    reasoningList.innerHTML = reasoning.map((r, i) => `
        <div class="reasoning-item">
            <span class="reasoning-num">${i + 1}.</span>
            <span>${escapeHtml(r)}</span>
        </div>
    `).join("");

    // Suggested Fix Card
    renderRemediationPlan(detail.remediation_plan, detail.remediation_suggestion, resource);
}

function renderCausalPaths(paths, rootCause) {
    if (!causalPathsSection || !causalPathsList) return;

    if (!paths || paths.length === 0) {
        causalPathsList.innerHTML = `<div style="font-size:0.75rem; color:var(--text-muted); font-style:italic;">Single-node anomaly with no cascading propagation.</div>`;
        return;
    }

    causalPathsList.innerHTML = paths.map(pathStr => {
        const steps = pathStr.split("->").map(s => s.trim());
        const renderedSteps = steps.map((s, idx) => {
            let pillClass = "causal-step-pill is-intermediate";
            if (idx === 0) pillClass = "causal-step-pill is-root";
            else if (idx === steps.length - 1) pillClass = "causal-step-pill is-leaf";
            return `<span class="${pillClass}">${escapeHtml(s)}</span>`;
        }).join(` <span class="causal-arrow-symbol">➔</span> `);

        return `<div class="causal-path-item">${renderedSteps}</div>`;
    }).join("");
}

function renderScoringBars(breakdown) {
    if (!scoringBars) return;
    const bd = breakdown || {};

    const dimensions = [
        { key: "temporal_correlation", name: "Temporal Origin (Failed First)", max: 25 },
        { key: "dependency_relationship", name: "Graph Topological Influence", max: 30 },
        { key: "downstream_symptoms", name: "Downstream Symptom Correlation", max: 20 },
        { key: "metric_anomaly_magnitude", name: "Metric Telemetry Anomaly", max: 15 },
        { key: "severity", name: "Alarm Severity Weight", max: 10 }
    ];

    scoringBars.innerHTML = dimensions.map(dim => {
        const score = Math.round(bd[dim.key] ?? 0);
        const pct = Math.min(100, Math.round((score / dim.max) * 100));

        return `
            <div class="evidence-bar-row">
                <div class="evidence-label-row">
                    <span>${dim.name}</span>
                    <strong>${score} / ${dim.max} pts</strong>
                </div>
                <div class="evidence-track">
                    <div class="evidence-fill" style="width: ${pct}%"></div>
                </div>
            </div>
        `;
    }).join("");
}

function renderCandidateRanking(allCandidates, rootCause, contributingCauses) {
    if (!candidateRankingBody) return;

    if (!allCandidates || allCandidates.length === 0) {
        candidateRankingBody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:1rem;">No candidate scores available.</td></tr>`;
        return;
    }

    const secondaryList = contributingCauses || [];

    candidateRankingBody.innerHTML = allCandidates.map((cand, idx) => {
        const isRoot = cand.service === rootCause;
        const isSecondary = secondaryList.includes(cand.service);
        const scoreVal = Math.round(cand.total ?? 0);

        let badgeClass = "candidate-badge-pill candidate-badge-symptom";
        let badgeText = "SYMPTOM";

        if (isRoot) {
            badgeClass = "candidate-badge-pill candidate-badge-root";
            badgeText = "PRIMARY ROOT";
        } else if (isSecondary) {
            badgeClass = "candidate-badge-pill candidate-badge-secondary";
            badgeText = "SECONDARY ROOT";
        }

        const barColor = isRoot ? "#DC2626" : isSecondary ? "#D97706" : "var(--indigo)";

        return `
            <tr>
                <td><strong style="color: ${isRoot ? 'var(--red)' : 'var(--text-muted)'};">#${idx + 1}</strong></td>
                <td><strong>${escapeHtml(cand.service)}</strong></td>
                <td>
                    <div class="candidate-score-bar-wrap">
                        <span style="font-weight:700; width:28px;">${scoreVal}%</span>
                        <div class="candidate-mini-track">
                            <div class="candidate-mini-fill" style="width: ${scoreVal}%; background: ${barColor};"></div>
                        </div>
                    </div>
                </td>
                <td><span class="${badgeClass}">${badgeText}</span></td>
            </tr>
        `;
    }).join("");
}

function renderRemediationPlan(plan, fallbackAction, resource) {
    const p = plan || {};
    const action = p.recommended_action || fallbackAction || "Trigger automated runbook";
    const mttr = p.estimated_mttr_minutes || 3;
    const actionType = p.action_type || "AUTO_HEAL";
    const commands = (p.commands && p.commands.length > 0) 
        ? p.commands.join("\n") 
        : `# Automated remediation runbook nominal\naws ec2 reboot-instances --instance-ids ${resource}`;

    if (fixActionSubtitle) fixActionSubtitle.textContent = action;
    if (fixEstimatedTime) fixEstimatedTime.textContent = `~${mttr} min to resolve`;

    if (fixActionTypeBadge) {
        fixActionTypeBadge.textContent = actionType;
        fixActionTypeBadge.style.display = "inline-block";
    }

    if (remediationCommand) remediationCommand.textContent = commands;

    // Safety Checks
    if (safetyChecksList && safetyChecksBox) {
        const checks = p.safety_checks || [
            "Verify target capacity is within cloud account limit quotas",
            "Ensure traffic is properly drained before restart or failover",
            "Validate telemetry alarm state transitions"
        ];

        safetyChecksList.innerHTML = checks.map(c => `
            <li>
                <span class="safety-check-icon">✓</span>
                <span>${escapeHtml(c)}</span>
            </li>
        `).join("");
        safetyChecksBox.style.display = "flex";
    }
}

// ---------------------------------------------------------------------------
// 15. Lifecycle Stepper
// ---------------------------------------------------------------------------
function updateLifecycleStepper(status) {
    if (!step1 || !step2 || !step3) return;
    const isResolved = status === "RESOLVED";
    const hasIncident = !!currentIncident;

    if (!hasIncident) {
        step1.className = "stepper-item is-active";
        step1.querySelector(".stepper-circle").textContent = "1";
        step2.className = "stepper-item";
        step2.querySelector(".stepper-circle").textContent = "2";
        step3.className = "stepper-item";
        step3.querySelector(".stepper-circle").textContent = "3";
    } else if (isResolved) {
        step1.className = "stepper-item is-done";
        step1.querySelector(".stepper-circle").textContent = "✓";
        step2.className = "stepper-item is-done";
        step2.querySelector(".stepper-circle").textContent = "✓";
        step3.className = "stepper-item is-done";
        step3.querySelector(".stepper-circle").textContent = "✓";
        step3.querySelector(".stepper-labels strong").style.color = "var(--emerald)";
        step3.querySelector(".stepper-labels span").textContent = "Fix verified in cloud";
    } else {
        step1.className = "stepper-item is-done";
        step1.querySelector(".stepper-circle").textContent = "✓";
        step2.className = "stepper-item is-done";
        step2.querySelector(".stepper-circle").textContent = "✓";
        step3.className = "stepper-item is-active";
        step3.querySelector(".stepper-circle").textContent = "3";
        step3.querySelector(".stepper-labels strong").style.color = "";
        step3.querySelector(".stepper-labels span").textContent = "Review suggested fix";
    }
}

// ---------------------------------------------------------------------------
// 16. Empty State (Zero Clean State)
// ---------------------------------------------------------------------------
function renderEmptyState() {
    kpiRootCause.textContent = "Healthy";
    kpiRootCause.className = "kpi-big-num text-success";
    kpiConfidenceBadge.textContent = "0% confidence";
    kpiBlastCount.textContent = "0";

    incidentTitle.textContent = "All Cloud Services Healthy";
    incidentResourceId.textContent = "All Nominals";
    if (incidentRegion) incidentRegion.textContent = "us-east-1";

    incidentStatusTag.textContent = "NO ANOMALIES";
    incidentStatusTag.className = "status-tag";
    incidentStatusTag.style.background = "#ECFDF5";
    incidentStatusTag.style.color = "#059669";
    incidentStatusTag.style.borderColor = "#A7F3D0";

    if (incidentCompoundBadge) incidentCompoundBadge.style.display = "none";

    diagnosisCauseTitle.textContent = "No Failures Detected";
    diagnosisResourceId.textContent = "All cloud services nominal";
    diagnosisConfidenceTag.textContent = "0% CONFIDENCE";
    diagnosisExplanation.textContent = "Architecture is operating within healthy performance and error budget thresholds.";

    if (causalPathsList) causalPathsList.innerHTML = `<div style="font-size:0.75rem; color:var(--text-muted);">No active anomaly propagation.</div>`;
    if (scoringBars) scoringBars.innerHTML = `<div style="font-size:0.75rem; color:#94A3B8;">No telemetry anomalies present in cloud.</div>`;
    if (candidateRankingBody) candidateRankingBody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:1rem;">All candidate services healthy.</td></tr>`;
    if (reasoningList) reasoningList.innerHTML = `<div style="font-size:0.75rem; color:#94A3B8;">Awaiting live incident telemetry from CloudWatch or injected custom faults...</div>`;

    if (fixActionSubtitle) fixActionSubtitle.textContent = "System nominal";
    if (fixEstimatedTime) fixEstimatedTime.textContent = "0 min";
    if (fixActionTypeBadge) fixActionTypeBadge.style.display = "none";
    if (remediationCommand) remediationCommand.textContent = "# System running normally - no remediation required";
    if (safetyChecksBox) safetyChecksBox.style.display = "none";

    updateLifecycleStepper("HEALTHY");
}

// ---------------------------------------------------------------------------
// 17. Timeline Filtering & Search
// ---------------------------------------------------------------------------
function filterAndRenderTimeline() {
    if (!timelineList) return;

    if (!allEvents || allEvents.length === 0) {
        timelineList.innerHTML = `<div class="timeline-empty-msg">No active anomaly events recorded yet. Inject a custom fault or select a scenario above to stream live telemetry.</div>`;
        if (timelineEventCount) timelineEventCount.textContent = "0 events recorded";
        return;
    }

    const query = (timelineSearchInput?.value || "").toLowerCase().trim();
    const selectedSev = timelineSeverityFilter?.value || "ALL";
    const selectedSvc = timelineServiceFilter?.value || "ALL";

    const filtered = allEvents.filter(e => {
        // Severity match
        if (selectedSev !== "ALL") {
            const sev = (e.severity || "").toUpperCase();
            if (selectedSev === "CRITICAL" && sev !== "CRITICAL") return false;
            if (selectedSev === "HIGH" && sev !== "HIGH" && sev !== "CRITICAL") return false;
            if (selectedSev === "MEDIUM" && sev === "LOW") return false;
        }

        // Service match
        if (selectedSvc !== "ALL" && e.service !== selectedSvc) {
            return false;
        }

        // Search query match
        if (query) {
            const matchStr = `${e.service} ${e.event_type} ${e.metric} ${e.resource_id} ${JSON.stringify(e.metadata || {})}`.toLowerCase();
            if (!matchStr.includes(query)) return false;
        }

        return true;
    });

    if (timelineEventCount) {
        timelineEventCount.textContent = filtered.length === allEvents.length 
            ? `${allEvents.length} events recorded` 
            : `${filtered.length} of ${allEvents.length} events`;
    }

    if (filtered.length === 0) {
        timelineList.innerHTML = `<div class="timeline-empty-msg">No events match the current filter criteria.</div>`;
        return;
    }

    timelineList.innerHTML = filtered.slice().reverse().map(e => {
        const isCrit = (e.severity || "").toUpperCase() === "CRITICAL";
        const isHigh = (e.severity || "").toUpperCase() === "HIGH";
        const rowClass = isCrit ? "timeline-item-row is-critical" : isHigh ? "timeline-item-row is-high" : "timeline-item-row";
        const timeStr = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : "+0s";

        return `
            <div class="${rowClass}">
                <div class="timeline-item-left">
                    <span class="timeline-source-pill">${escapeHtml(e.source || "AWS")}</span>
                    <strong>${escapeHtml(e.service)}</strong>
                    <span>· ${escapeHtml(e.event_type || "Anomaly")}</span>
                    ${e.metric ? `<small style="color:#64748B;">(${escapeHtml(e.metric)}: ${e.value ?? 'peaked'})</small>` : ''}
                </div>
                <span class="timeline-time-str">${timeStr}</span>
            </div>
        `;
    }).join("");
}

// ---------------------------------------------------------------------------
// 18. Apply Automated Remediation (POST /api/incidents/{id}/mitigate)
// ---------------------------------------------------------------------------
async function applyRemediationFix() {
    if (!currentIncident) return;

    btnApplyFix.disabled = true;
    fixStatusMsg.textContent = "Executing automated remediation runbook...";
    fixStatusMsg.style.color = "#D97706";

    try {
        const res = await fetch(`${API_BASE}/incidents/${currentIncident.incident_id}/mitigate`, {
            method: "POST"
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Remediation failed");

        currentIncident.status = "RESOLVED";
        incidentStatusTag.textContent = "RESOLVED";
        incidentStatusTag.className = "status-tag status-tag-resolved";
        updateLifecycleStepper("RESOLVED");

        fixStatusMsg.textContent = `✓ ${data.message || "Remediation applied! Incident marked resolved in cloud."}`;
        fixStatusMsg.style.color = "#059669";
    } catch (err) {
        console.error("Mitigate error:", err);
        fixStatusMsg.textContent = `Error: ${err.message}`;
        fixStatusMsg.style.color = "#DC2626";
    } finally {
        btnApplyFix.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// 19. Reset System (POST /api/reset)
// ---------------------------------------------------------------------------
async function resetSystem() {
    try {
        if (fixStatusMsg) fixStatusMsg.textContent = "";
        clearGraphBlastHighlight();
        await fetch(`${API_BASE}/reset`, { method: "POST" });
        await fetchDashboardData();
    } catch (e) {
        console.error("Reset error:", e);
    }
}

// ---------------------------------------------------------------------------
// 20. AWS Cloud Architecture Live Status & S3 / SNS Integrations (Review 1)
// ---------------------------------------------------------------------------
async function fetchCloudArchitectureStatus() {
    try {
        const res = await fetch(`${API_BASE}/cloud/status`);
        if (!res.ok) return;
        const data = await res.json();
        const comp = data.components || {};

        const s3Label = document.getElementById("awsS3Label");
        if (s3Label && comp.audit_archive) {
            s3Label.textContent = comp.audit_archive.bucket_name || "cloudlens-incident-archive";
        }

        const snsLabel = document.getElementById("awsSnsLabel");
        if (snsLabel && comp.notifications) {
            snsLabel.textContent = (comp.notifications.topic_arn || "cloudlens-notifications").split(":").pop();
        }
    } catch (e) {
        console.warn("Could not fetch cloud architecture status:", e);
    }
}

async function openS3PostMortemModal() {
    const modal = document.getElementById("s3PostMortemModal");
    const contentEl = document.getElementById("s3ModalContent");
    const uriEl = document.getElementById("s3ModalUri");
    const bucketEl = document.getElementById("s3ModalBucket");

    if (!modal || !contentEl) return;

    modal.style.display = "flex";
    contentEl.textContent = "Loading post-mortem from Amazon S3 bucket...";

    const incId = currentIncident ? currentIncident.incident_id : "inc_sample";

    try {
        const res = await fetch(`${API_BASE}/s3/reports/${incId}`);
        if (res.ok) {
            const data = await res.json();
            if (uriEl) uriEl.textContent = `s3://${data.metadata?.s3_bucket || 'cloudlens-incident-archive'}/incidents/${incId}/post_mortem.json`;
            if (bucketEl) bucketEl.textContent = data.metadata?.s3_bucket || 'cloudlens-incident-archive';
            contentEl.textContent = JSON.stringify(data, null, 2);
        } else {
            // Provide current incident export
            const currentData = {
                metadata: {
                    platform: "CloudLens Autonomous RCA (Enterprise)",
                    incident_id: incId,
                    s3_bucket: "cloudlens-incident-archive",
                    archived_at: new Date().toISOString()
                },
                incident: currentIncident,
                analysis: currentIncidentDetail
            };
            if (uriEl) uriEl.textContent = `s3://cloudlens-incident-archive/incidents/${incId}/post_mortem.json`;
            contentEl.textContent = JSON.stringify(currentData, null, 2);
        }
    } catch (e) {
        contentEl.textContent = `Error loading S3 post-mortem: ${e.message}`;
    }
}

function closeS3PostMortemModal() {
    const modal = document.getElementById("s3PostMortemModal");
    if (modal) modal.style.display = "none";
}

function copyS3ModalContent() {
    const contentEl = document.getElementById("s3ModalContent");
    const copyBtn = document.getElementById("btnCopyS3Json");
    if (contentEl) {
        navigator.clipboard.writeText(contentEl.textContent);
        if (copyBtn) {
            copyBtn.textContent = "Copied!";
            setTimeout(() => { copyBtn.textContent = "Copy JSON"; }, 2000);
        }
    }
}

async function triggerTestSnsAlert() {
    const btn = document.getElementById("btnTestSnsAlert");
    if (btn) btn.disabled = true;
    try {
        const incId = currentIncident ? currentIncident.incident_id : "inc_demo";
        const res = await fetch(`${API_BASE}/sns/test?incident_id=${incId}`, { method: "POST" });
        const data = await res.json();
        alert(`🔔 Amazon SNS Alert Dispatched Successfully!\n\nSubject: ${data.subject}\nStatus: ${data.status}\nTopic: ${data.topic_arn}`);
    } catch (e) {
        alert(`Failed to dispatch SNS alert: ${e.message}`);
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// 21. Real-Time WebSocket Streaming & AWS Live Asset Auto-Discovery
// ---------------------------------------------------------------------------
let liveWebSocket = null;

function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = (window.location.port === "8000" && window.location.protocol !== "file:")
        ? window.location.host
        : "127.0.0.1:8000";
    const wsUrl = `${protocol}//${host}/ws/stream`;

    try {
        liveWebSocket = new WebSocket(wsUrl);
        liveWebSocket.onopen = () => {
            console.log("WebSocket connected for real-time live telemetry streaming.");
        };
        liveWebSocket.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                console.log("Live stream message:", msg.type);
                if (msg.type === "INCIDENT_UPDATED" || msg.type === "TOPOLOGY_MUTATED") {
                    fetchDashboardData().catch(() => {});
                }
            } catch (e) {}
        };
        liveWebSocket.onclose = () => {
            // Auto reconnect after 3 seconds
            setTimeout(initWebSocket, 3000);
        };
    } catch (e) {
        console.warn("WebSocket connection skipped:", e);
    }
}

async function discoverAwsAssets() {
    const btn = document.getElementById("btnDiscoverAws");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Scanning AWS Assets...";
    }
    try {
        const res = await fetch(`${API_BASE}/topology/discover`, { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            alert(`🔍 AWS Infrastructure Auto-Discovery Complete!\n\nRegion: ${data.region}\nDiscovered Assets: ${data.total_assets_discovered}\n\nLive cloud nodes have been injected into your dynamic topology.`);
            await fetchDashboardData();
        } else {
            alert(`Discovery error: ${data.detail || 'Failed'}`);
        }
    } catch (e) {
        alert(`AWS Discovery error: ${e.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🔍 Discover AWS Assets";
        }
    }
}