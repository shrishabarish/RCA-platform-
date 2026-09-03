// CloudLens Enterprise RCA Engine - Frontend Controller

const API_BASE = "/api";

// State
let currentIncident = null;
let currentIncidentDetail = null;
let currentTopology = null;
let allEvents = [];

// Node Positions for the Topology Canvas
const NODE_POSITIONS = {
    "EC2": { x: 140, y: 110, icon: "🖥️" },
    "DynamoDB": { x: 140, y: 240, icon: "🗄️" },
    "Auth": { x: 140, y: 370, icon: "🔐" },
    "Worker": { x: 350, y: 340, icon: "⚙️" },
    "API": { x: 350, y: 170, icon: "🌐" },
    "Application": { x: 560, y: 240, icon: "💻" }
};

// Escape any value that came from user input or the API before it is
// dropped into innerHTML, so a typed-in resource ID etc. can never run as HTML.
function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
}

// Advance the guided workflow strip at the top of the page.
// step: 1 = about to break something, 2 = investigating, 3 = fix applied.
function setWorkflowStep(step) {
    for (let i = 1; i <= 3; i++) {
        const el = document.getElementById(`workflowStep${i}`);
        if (!el) continue;
        el.classList.toggle("is-active", i === step);
        el.classList.toggle("is-done", i < step);
    }
}

// DOM Elements
const scenarioSelect = document.getElementById("scenarioSelect");
const btnSimulate = document.getElementById("btnSimulate");
const btnCustomEvent = document.getElementById("btnCustomEvent");
const btnReset = document.getElementById("btnReset");
const btnExecuteMitigation = document.getElementById("btnExecuteMitigation");
const mitigationStatus = document.getElementById("mitigationStatus");

const kpiEventCount = document.getElementById("kpiEventCount");
const kpiIncidentCount = document.getElementById("kpiIncidentCount");
const kpiRootCause = document.getElementById("kpiRootCause");

const topologySvg = document.getElementById("topologySvg");
const whatIfContent = document.getElementById("whatIfContent");
const whatIfRiskBadge = document.getElementById("whatIfRiskBadge");

const rcaServiceName = document.getElementById("rcaServiceName");
const confidenceCirclePath = document.getElementById("confidenceCirclePath");
const confidenceText = document.getElementById("confidenceText");
const affectedServicesBadges = document.getElementById("affectedServicesBadges");
const causalPathsContainer = document.getElementById("causalPathsContainer");
const scoringBars = document.getElementById("scoringBars");
const reasoningChainBox = document.getElementById("reasoningChainBox");
const candidateTableBody = document.getElementById("candidateTableBody");
const remediationTypeBadge = document.getElementById("remediationTypeBadge");
const remediationDesc = document.getElementById("remediationDesc");
const remediationCommand = document.getElementById("remediationCommand");
const timelineContainer = document.getElementById("timelineContainer");
const eventCountBadge = document.getElementById("eventCountBadge");
const incidentSeverityBadge = document.getElementById("incidentSeverityBadge");

const customEventModal = document.getElementById("customEventModal");
const btnCloseModal = document.getElementById("btnCloseModal");
const customEventForm = document.getElementById("customEventForm");

const awsStatusBadge = document.getElementById("awsStatusBadge");
const btnPollAws = document.getElementById("btnPollAws");
const compoundIncidentBadge = document.getElementById("compoundIncidentBadge");
const contributingCausesRow = document.getElementById("contributingCausesRow");
const remediationMttrChip = document.getElementById("remediationMttrChip");
const remediationSafetyChecks = document.getElementById("remediationSafetyChecks");

// Initialization
document.addEventListener("DOMContentLoaded", () => {
    setupEventListeners();
    fetchDashboardData();
    fetchAwsStatus();
    loadScenarioOptions();
});

function setupEventListeners() {
    btnSimulate.addEventListener("click", () => {
        const scenario = scenarioSelect.value;
        triggerScenario(scenario);
    });

    scenarioSelect.addEventListener("change", (e) => {
        triggerScenario(e.target.value);
    });

    btnReset.addEventListener("click", resetSystem);
    btnExecuteMitigation.addEventListener("click", executeMitigation);
    if (btnPollAws) btnPollAws.addEventListener("click", pollLiveAws);

    btnCustomEvent.addEventListener("click", () => {
        customEventModal.classList.remove("hidden");
    });

    btnCloseModal.addEventListener("click", () => {
        customEventModal.classList.add("hidden");
    });

    customEventForm.addEventListener("submit", handleCustomEventSubmit);
}

// Fetch Full Dashboard State
async function fetchDashboardData() {
    try {
        const [incidentsRes, eventsRes] = await Promise.all([
            fetch(`${API_BASE}/incidents`),
            fetch(`${API_BASE}/events`)
        ]);
        if (!incidentsRes.ok || !eventsRes.ok) {
            throw new Error(`Dashboard fetch failed (${incidentsRes.status}/${eventsRes.status})`);
        }

        const incidents = await incidentsRes.json();
        allEvents = await eventsRes.json();

        currentIncident = incidents.length > 0 ? incidents[0] : null;

        if (currentIncident) {
            const detailRes = await fetch(`${API_BASE}/incidents/${currentIncident.incident_id}`);
            currentIncidentDetail = detailRes.ok ? await detailRes.json() : null;
        } else {
            currentIncidentDetail = null;
        }

        const topologyRes = await fetch(`${API_BASE}/topology${currentIncident ? `?incident_id=${currentIncident.incident_id}` : ''}`);
        currentTopology = topologyRes.ok ? await topologyRes.json() : null;

        updateKPIs(allEvents, incidents);
        renderTopology(currentTopology);
        renderRCA(currentIncidentDetail || currentIncident);
        renderTimeline(allEvents);
        renderWhatIf(currentIncidentDetail ? currentIncidentDetail.probable_root_cause : "EC2");
        setWorkflowStep(currentIncident ? 2 : 1);
    } catch (err) {
        console.error("Error fetching dashboard data:", err);
        showMitigationStatus("Couldn't load the latest data — check the API and try again.", "text-rose");
    }
}

// Check whether the backend has a live AWS connection (GET /api/aws/status)
async function fetchAwsStatus() {
    if (!awsStatusBadge) return;
    try {
        const res = await fetch(`${API_BASE}/aws/status`);
        const data = await res.json();
        if (data.connected) {
            awsStatusBadge.textContent = `AWS connected · ${escapeHtml(data.region || "")}`;
            awsStatusBadge.className = "badge badge-heal";
            awsStatusBadge.title = data.arn ? `Authenticated as ${data.arn}` : "";
            if (btnPollAws) btnPollAws.disabled = false;
        } else {
            awsStatusBadge.textContent = "AWS not connected";
            awsStatusBadge.className = "badge badge-subtle";
            awsStatusBadge.title = data.message || data.error || "";
            if (btnPollAws) btnPollAws.disabled = false; // still clickable; backend will explain why it can't poll
        }
    } catch (err) {
        console.warn("Could not check AWS status:", err);
        awsStatusBadge.textContent = "AWS status unknown";
        awsStatusBadge.className = "badge badge-subtle";
    }
}

// Pull real CloudWatch/CloudTrail signal into the pipeline (POST /api/aws/poll)
async function pollLiveAws() {
    if (!btnPollAws) return;
    btnPollAws.disabled = true;
    const originalLabel = btnPollAws.innerHTML;
    btnPollAws.innerHTML = `<span class="spinner"></span> Checking AWS...`;
    try {
        const res = await fetch(`${API_BASE}/aws/poll`, { method: "POST" });
        if (!res.ok) throw new Error(`AWS poll failed with status ${res.status}`);
        await fetchDashboardData();
        showMitigationStatus("Pulled the latest signal from AWS.", "text-emerald");
    } catch (err) {
        console.error("AWS poll failed:", err);
        showMitigationStatus("Couldn't reach AWS — check that credentials are configured.", "text-rose");
    } finally {
        btnPollAws.disabled = false;
        btnPollAws.innerHTML = originalLabel;
    }
}

// Replace the static scenario list with whatever the backend actually offers,
// if it's shaped in a way we recognize. Falls back to the built-in options
// (already in the HTML) if the endpoint is missing or shaped differently.
async function loadScenarioOptions() {
    try {
        const res = await fetch(`${API_BASE}/scenarios`);
        if (!res.ok) return;
        const data = await res.json();
        const list = Array.isArray(data) ? data : (Array.isArray(data.scenarios) ? data.scenarios : null);
        if (!list || list.length === 0) return;

        const options = list.map(item => {
            if (typeof item === "string") return { id: item, label: item };
            const id = item.id || item.scenario_id || item.key;
            const label = item.label || item.name || item.title || item.description || id;
            return id ? { id, label } : null;
        }).filter(Boolean);

        if (options.length === 0) return; // unrecognized shape — keep the built-in list

        scenarioSelect.innerHTML = options
            .map(o => `<option value="${escapeHtml(o.id)}">${escapeHtml(o.label)}</option>`)
            .join("");
    } catch (err) {
        console.warn("Could not load scenario list, using built-in options:", err);
    }
}


async function triggerScenario(scenarioId) {
    btnSimulate.disabled = true;
    btnSimulate.innerHTML = `<span class="spinner"></span> Simulating...`;

    try {
        const res = await fetch(`${API_BASE}/scenarios/${scenarioId}/simulate`, {
            method: "POST"
        });
        if (!res.ok) throw new Error(`Simulate failed with status ${res.status}`);
        await fetchDashboardData();
        showMitigationStatus(`Scenario running: ${scenarioId.toUpperCase()}`, "text-emerald");
    } catch (err) {
        console.error("Simulation failed:", err);
        showMitigationStatus("Couldn't run that scenario — try again.", "text-rose");
    } finally {
        btnSimulate.disabled = false;
        btnSimulate.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Simulate Scenario`;
    }
}

// Reset System State
async function resetSystem() {
    try {
        const res = await fetch(`${API_BASE}/reset`, { method: "POST" });
        if (!res.ok) throw new Error(`Reset failed with status ${res.status}`);
        await fetchDashboardData();
        setWorkflowStep(1);
        showMitigationStatus("Back to a clean slate.", "text-muted");
    } catch (err) {
        console.error("Reset failed:", err);
        showMitigationStatus("Couldn't reset — try again.", "text-rose");
    }
}

// Execute Auto Remediation
async function executeMitigation() {
    if (!currentIncident) return;

    btnExecuteMitigation.disabled = true;
    btnExecuteMitigation.innerHTML = `⚡ Applying Auto-Remediation...`;

    try {
        const res = await fetch(`${API_BASE}/incidents/${currentIncident.incident_id}/mitigate`, {
            method: "POST"
        });
        if (!res.ok) throw new Error(`Mitigation failed with status ${res.status}`);
        const result = await res.json();
        showMitigationStatus(`✓ ${result.message}`, "text-emerald");
        setWorkflowStep(3);
        await fetchDashboardData();
    } catch (err) {
        console.error("Mitigation error:", err);
        showMitigationStatus("Couldn't apply the fix — try again.", "text-rose");
    } finally {
        btnExecuteMitigation.disabled = false;
        btnExecuteMitigation.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Execute Auto-Remediation`;
    }
}

// Handle Custom Event Submission
async function handleCustomEventSubmit(e) {
    e.preventDefault();
    const service = document.getElementById("inpService").value;
    const severity = document.getElementById("inpSeverity").value;
    const eventType = document.getElementById("inpEventType").value;
    const resourceId = document.getElementById("inpResourceId").value;
    const metric = document.getElementById("inpMetric").value;
    const value = parseFloat(document.getElementById("inpValue").value);

    const eventPayload = {
        event_id: `evt_cust_${Date.now()}`,
        timestamp: new Date().toISOString(),
        source: "application",
        event_type: eventType,
        service: service,
        resource_id: resourceId,
        metric: metric,
        value: value,
        severity: severity,
        metadata: { injected: "custom_ui" }
    };

    try {
        const res = await fetch(`${API_BASE}/events`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(eventPayload)
        });
        if (!res.ok) throw new Error(`Event send failed with status ${res.status}`);

        customEventModal.classList.add("hidden");
        await fetchDashboardData();
        showMitigationStatus(`Event sent for ${escapeHtml(service)}`, "text-emerald");
    } catch (err) {
        console.error("Event injection failed:", err);
        showMitigationStatus("Couldn't send that event — try again.", "text-rose");
    }
}

// UI Renderers
function updateKPIs(events, incidents) {
    kpiEventCount.textContent = events.length;
    kpiIncidentCount.textContent = incidents.length;
    if (currentIncident && currentIncident.probable_root_cause) {
        kpiRootCause.textContent = currentIncident.probable_root_cause;
        kpiRootCause.classList.remove("text-emerald");
        kpiRootCause.classList.add("text-rose");
    } else {
        kpiRootCause.textContent = "Healthy";
        kpiRootCause.classList.remove("text-rose");
        kpiRootCause.classList.add("text-emerald");
    }
}

function renderTopology(topology) {
    if (!topology || !topology.nodes) return;

    let svgHtml = `
        <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="28" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="rgba(255, 255, 255, 0.25)" />
            </marker>
            <filter id="glow-root" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="6" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
        </defs>
    `;

    // Render Edges
    topology.edges.forEach(edge => {
        const srcPos = NODE_POSITIONS[edge.source];
        const tgtPos = NODE_POSITIONS[edge.target];
        if (srcPos && tgtPos) {
            svgHtml += `
                <line x1="${srcPos.x}" y1="${srcPos.y}" x2="${tgtPos.x}" y2="${tgtPos.y}" 
                      stroke="rgba(255,255,255,0.15)" stroke-width="2" stroke-dasharray="4"
                      marker-end="url(#arrow)" />
            `;
        }
    });

    // Render Nodes
    topology.nodes.forEach(node => {
        const pos = NODE_POSITIONS[node.id] || { x: 300, y: 200, icon: "📦" };
        let nodeColor = "#10b981"; // Healthy
        let statusClass = "node-healthy";
        let filterAttr = "";

        if (node.status === "ROOT_CAUSE") {
            nodeColor = "#f43f5e";
            statusClass = "node-root";
            filterAttr = 'filter="url(#glow-root)"';
        } else if (node.status === "DEGRADED") {
            nodeColor = "#f59e0b";
            statusClass = "node-degraded";
        }

        const isRoot = node.status === "ROOT_CAUSE";
        const radius = isRoot ? 32 : Math.round(24 + (typeof node.criticality === "number" ? node.criticality : 0.6) * 12);

        svgHtml += `
            <g class="topology-node-group ${statusClass}" transform="translate(${pos.x}, ${pos.y})" onclick="renderWhatIf('${node.id}')" style="cursor: pointer;">
                <circle r="${radius}" fill="#111827" stroke="${nodeColor}" stroke-width="2.5" ${filterAttr} />
                <text text-anchor="middle" dy="-4" font-size="16">${pos.icon}</text>
                <text text-anchor="middle" dy="14" fill="#f8fafc" font-size="11" font-weight="700" font-family="Inter, sans-serif">${escapeHtml(node.id)}</text>
                <text text-anchor="middle" dy="48" fill="#94a3b8" font-size="9" font-weight="500">${escapeHtml(node.label.length > 18 ? node.label.substring(0, 16) + '...' : node.label)}</text>
            </g>
        `;
    });

    topologySvg.innerHTML = svgHtml;
}

async function renderWhatIf(serviceName) {
    if (!serviceName) serviceName = "EC2";
    try {
        const res = await fetch(`${API_BASE}/what-if/${serviceName}`);
        if (res.ok) {
            const data = await res.json();
            whatIfRiskBadge.textContent = `Risk: ${data.estimated_architecture_risk_score}/100`;
            whatIfRiskBadge.className = data.estimated_architecture_risk_score > 70 ? 'badge badge-critical' : 'badge badge-medium';
            whatIfContent.innerHTML = `
                Target: <strong>${data.target_service}</strong> | Criticality: <strong>${data.criticality_level}</strong><br>
                Direct Casualties: <strong>${data.direct_downstream_services.join(', ') || 'None'}</strong><br>
                Total Blast Radius: <strong>${data.all_impacted_services.join(', ') || 'Isolated'}</strong> (${data.total_blast_radius_count} services)
            `;
        }
    } catch (e) {
        console.warn("What-if query error:", e);
    }
}

function renderRCA(incident) {
    if (!incident || !incident.probable_root_cause) {
        rcaServiceName.textContent = "Everything looks healthy";
        rcaServiceName.classList.remove("text-rose");
        rcaServiceName.classList.add("text-emerald");
        confidenceText.textContent = "0%";
        confidenceCirclePath.setAttribute("stroke-dasharray", "0, 100");
        affectedServicesBadges.innerHTML = `<span class="badge badge-subtle">None</span>`;
        causalPathsContainer.innerHTML = `<span class="text-muted" style="font-size:0.78rem;">No failure is spreading right now.</span>`;
        scoringBars.innerHTML = `<p class="text-muted" style="font-size:0.8rem;">Nothing unusual detected.</p>`;
        reasoningChainBox.innerHTML = `<span class="text-muted">Everything is running within its normal range.</span>`;
        candidateTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;">No anomalies</td></tr>`;
        remediationDesc.textContent = "Nothing needs fixing right now.";
        remediationCommand.textContent = "# All healthy — no action needed";
        incidentSeverityBadge.textContent = "HEALTHY";
        incidentSeverityBadge.className = "badge badge-heal";
        return;
    }

    rcaServiceName.textContent = incident.probable_root_cause;
    rcaServiceName.classList.remove("text-emerald");
    rcaServiceName.classList.add("text-rose");

    if (compoundIncidentBadge) {
        compoundIncidentBadge.style.display = incident.is_compound_incident ? "inline-flex" : "none";
    }
    if (contributingCausesRow) {
        if (incident.is_compound_incident && incident.contributing_causes && incident.contributing_causes.length > 0) {
            contributingCausesRow.style.display = "flex";
            contributingCausesRow.innerHTML = `<span class="subtext">Also contributing:</span> ` +
                incident.contributing_causes.map(c => `<span class="badge badge-subtle">${escapeHtml(c)}</span>`).join(" ");
        } else {
            contributingCausesRow.style.display = "none";
            contributingCausesRow.innerHTML = "";
        }
    }

    const conf = Math.round(incident.confidence || 0);
    confidenceText.textContent = `${conf}%`;
    confidenceCirclePath.setAttribute("stroke-dasharray", `${conf}, 100`);

    incidentSeverityBadge.textContent = incident.severity;
    incidentSeverityBadge.className = `badge badge-${incident.severity.toLowerCase()}`;

    // Downstream Casualties Badges
    if (incident.affected_services && incident.affected_services.length > 0) {
        affectedServicesBadges.innerHTML = incident.affected_services
            .map(s => `<span class="badge badge-medium">${escapeHtml(s)}</span>`)
            .join("");
    } else {
        affectedServicesBadges.innerHTML = `<span class="badge badge-subtle">Isolated (No downstream cascade)</span>`;
    }

    // Causal Propagation Paths
    if (incident.causal_paths && incident.causal_paths.length > 0) {
        causalPathsContainer.innerHTML = incident.causal_paths.map(p => `
            <div class="causal-path-chip">
                <span>↳</span> ${escapeHtml(p)}
            </div>
        `).join("");
    } else {
        causalPathsContainer.innerHTML = `<span class="text-muted" style="font-size:0.78rem;">Direct root impact.</span>`;
    }

    // Scoring Breakdown Bars
    const breakdown = incident.score_breakdown || {};
    const factorConfigs = [
        { key: "temporal_correlation", name: "It started first", max: 25 },
        { key: "dependency_relationship", name: "Everything else depends on it", max: 30 },
        { key: "downstream_symptoms", name: "Matches the other symptoms", max: 20 },
        { key: "metric_anomaly_magnitude", name: "How unusual its metrics were", max: 15 },
        { key: "severity", name: "How severe the alert was", max: 10 },
    ];

    let barsHtml = "";
    factorConfigs.forEach(fc => {
        const val = breakdown[fc.key] || 0;
        const pct = Math.min((val / fc.max) * 100, 100);
        barsHtml += `
            <div class="score-row">
                <div class="score-row-labels">
                    <span class="score-factor-name">${fc.name}</span>
                    <span class="score-factor-val">${val} / ${fc.max} pts</span>
                </div>
                <div class="score-progress-bg">
                    <div class="score-progress-bar" style="width: ${pct}%"></div>
                </div>
            </div>
        `;
    });
    scoringBars.innerHTML = barsHtml;

    // Mathematical Reasoning Chain / Audit Trail
    if (incident.reasoning_chain && incident.reasoning_chain.length > 0) {
        reasoningChainBox.innerHTML = incident.reasoning_chain.map(step => `
            <div class="reasoning-step">${escapeHtml(step)}</div>
        `).join("");
    } else {
        reasoningChainBox.innerHTML = `<span class="text-muted">Automated causal reasoning applied.</span>`;
    }

    // Candidate Comparison Table
    if (incident.all_candidate_scores && incident.all_candidate_scores.length > 0) {
        candidateTableBody.innerHTML = incident.all_candidate_scores.map((cand, idx) => {
            const isRoot = cand.service === incident.probable_root_cause;
            const bd = cand.breakdown || {};
            return `
                <tr class="${isRoot ? 'highlight-row' : ''}">
                    <td>#${idx + 1}</td>
                    <td><strong>${escapeHtml(cand.service)}</strong> ${isRoot ? '★' : ''}</td>
                    <td>${bd.temporal_correlation || 10} pts</td>
                    <td>${bd.dependency_relationship || 0} pts</td>
                    <td>${bd.metric_anomaly_magnitude || 10} pts</td>
                    <td><strong>${cand.total}%</strong></td>
                </tr>
            `;
        }).join("");
    } else {
        fetchCandidateScores(incident);
    }

    // Remediation Card
    if (incident.remediation_plan) {
        const plan = incident.remediation_plan;
        remediationDesc.textContent = plan.recommended_action;
        remediationTypeBadge.textContent = plan.action_type;
        remediationCommand.textContent = plan.commands.join("\n");

        if (remediationMttrChip) {
            if (typeof plan.estimated_mttr_minutes === "number") {
                remediationMttrChip.style.display = "inline-flex";
                remediationMttrChip.textContent = `~${plan.estimated_mttr_minutes} min to resolve`;
            } else {
                remediationMttrChip.style.display = "none";
            }
        }

        if (remediationSafetyChecks) {
            if (plan.safety_checks && plan.safety_checks.length > 0) {
                remediationSafetyChecks.style.display = "block";
                remediationSafetyChecks.innerHTML = `<span class="subtext">Before you run this:</span>` +
                    plan.safety_checks.map(c => `<div class="safety-check-item">${escapeHtml(c)}</div>`).join("");
            } else {
                remediationSafetyChecks.style.display = "none";
                remediationSafetyChecks.innerHTML = "";
            }
        }
    } else {
        fetchRemediation(incident.incident_id);
    }
}

async function fetchCandidateScores(incident) {
    // The backend didn't send a candidate comparison for this incident.
    // Show that plainly instead of inventing scores the model never computed.
    candidateTableBody.innerHTML = `
        <tr><td colspan="6" style="text-align:center;">
            No comparison data for this incident yet.
        </td></tr>
    `;
}

async function fetchRemediation(incidentId) {
    try {
        const res = await fetch(`${API_BASE}/incidents/${incidentId}/remediation`);
        if (res.ok) {
            const plan = await res.json();
            remediationDesc.textContent = plan.recommended_action;
            remediationTypeBadge.textContent = plan.action_type;
            remediationCommand.textContent = plan.commands.join("\n");
            if (remediationMttrChip) {
                if (typeof plan.estimated_mttr_minutes === "number") {
                    remediationMttrChip.style.display = "inline-flex";
                    remediationMttrChip.textContent = `~${plan.estimated_mttr_minutes} min to resolve`;
                } else {
                    remediationMttrChip.style.display = "none";
                }
            }
            if (remediationSafetyChecks) {
                if (plan.safety_checks && plan.safety_checks.length > 0) {
                    remediationSafetyChecks.style.display = "block";
                    remediationSafetyChecks.innerHTML = `<span class="subtext">Before you run this:</span>` +
                        plan.safety_checks.map(c => `<div class="safety-check-item">${escapeHtml(c)}</div>`).join("");
                } else {
                    remediationSafetyChecks.style.display = "none";
                    remediationSafetyChecks.innerHTML = "";
                }
            }
        }
    } catch (e) {
        console.warn("Could not fetch remediation plan:", e);
    }
}

function renderTimeline(events) {
    eventCountBadge.textContent = `${events.length} Events`;

    if (events.length === 0) {
        timelineContainer.innerHTML = `<p class="text-muted" style="font-size:0.8rem;">No events received.</p>`;
        return;
    }

    const sorted = [...events].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    const baseTime = new Date(sorted[0].timestamp).getTime();

    timelineContainer.innerHTML = sorted.map((evt, idx) => {
        const offsetSec = Math.round((new Date(evt.timestamp).getTime() - baseTime) / 1000);
        const isRoot = idx === 0;

        return `
            <div class="timeline-item ${isRoot ? 'root-origin' : ''}">
                <div class="timeline-item-header">
                    <span class="timeline-service-tag">${escapeHtml(evt.service)} (${escapeHtml(evt.resource_id)})</span>
                    <span class="timeline-time-offset">+${offsetSec}s</span>
                </div>
                <div class="timeline-event-type">${escapeHtml(evt.event_type)}</div>
                <div class="timeline-meta-row">
                    <span>${evt.metric ? `${escapeHtml(evt.metric)}: ${escapeHtml(evt.value)}` : escapeHtml(evt.source)}</span>
                    <span class="badge badge-${escapeHtml(evt.severity).toLowerCase()}">${escapeHtml(evt.severity)}</span>
                </div>
            </div>
        `;
    }).join("");
}

let mitigationStatusTimer = null;
function showMitigationStatus(msg, colorClass) {
    mitigationStatus.textContent = msg;
    mitigationStatus.className = `mitigation-status-msg ${colorClass}`;
    if (mitigationStatusTimer) clearTimeout(mitigationStatusTimer);
    mitigationStatusTimer = setTimeout(() => {
        mitigationStatus.textContent = "";
        mitigationStatusTimer = null;
    }, 6000);
}