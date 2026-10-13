const fs = require('fs');

const casePath = 'evaluation/cases/independent/indep_004_slow_burn_decoy_12node.json';
const rawCase = fs.readFileSync(casePath, 'utf8');
const caseData = JSON.parse(rawCase);

const htmlPath = 'frontend/index.html';
const htmlContent = fs.readFileSync(htmlPath, 'utf8');

// Benchmark: Replay loading of 12-node incident
const times = [];
for (let i = 0; i < 100; i++) {
    const t0 = performance.now();

    // 1. Ingest & deserialize replay case
    const parsed = JSON.parse(rawCase);
    const nodes = parsed.topology.nodes;
    const edges = parsed.topology.edges;
    const events = parsed.events;

    // 2. Compute dynamic layout
    const nodeIds = nodes.map(n => n.id);
    const inDegree = {};
    const adj = {};
    nodeIds.forEach(id => { inDegree[id] = 0; adj[id] = []; });
    edges.forEach(e => {
        if (inDegree[e.target] !== undefined && adj[e.source]) {
            inDegree[e.target]++;
            adj[e.source].push(e.target);
        }
    });

    const tiers = [];
    const visited = new Set();
    let currentTier = nodeIds.filter(id => inDegree[id] === 0);
    if (currentTier.length === 0) currentTier = [nodeIds[0]];
    while (currentTier.length > 0 && visited.size < nodeIds.length) {
        tiers.push(currentTier);
        currentTier.forEach(id => visited.add(id));
        const nextTierSet = new Set();
        currentTier.forEach(id => {
            (adj[id] || []).forEach(t => {
                if (!visited.has(t)) nextTierSet.add(t);
            });
        });
        currentTier = Array.from(nextTierSet);
    }
    const unvisited = nodeIds.filter(id => !visited.has(id));
    if (unvisited.length > 0) tiers.push(unvisited);

    const numCols = Math.max(tiers.length, 3);
    const maxRows = Math.max(...tiers.map(t => t.length), 3);
    const colWidth = 210, rowHeight = 82, paddingX = 90, paddingY = 55;
    const width = Math.max(780, numCols * colWidth + paddingX);
    const height = Math.max(440, maxRows * rowHeight + paddingY * 2);

    const nodeMap = {};
    tiers.forEach((tier, colIdx) => {
        const x = paddingX + colIdx * colWidth;
        const totalHeight = (tier.length - 1) * rowHeight;
        const startY = (height - totalHeight) / 2;
        tier.forEach((id, rowIdx) => {
            const y = startY + rowIdx * rowHeight;
            const nodeObj = nodes.find(n => n.id === id) || {};
            nodeMap[id] = { x, y, title: nodeObj.label || id, type: nodeObj.type };
        });
    });

    // 3. Render 12-node SVG elements
    let svgNodes = '';
    nodes.forEach(n => {
        const pos = nodeMap[n.id];
        svgNodes += `<g id="nodeGroup_${n.id}"><rect x="${pos.x}" y="${pos.y}" width="135" height="54"/><text>${pos.title}</text></g>`;
    });

    let svgEdges = '';
    edges.forEach(e => {
        const src = nodeMap[e.source];
        const tgt = nodeMap[e.target];
        if (src && tgt) {
            svgEdges += `<path d="M ${src.x} ${src.y} L ${tgt.x} ${tgt.y}" />`;
        }
    });

    // 4. Render 12-node timeline
    const timelineHtml = events.map(e => `
        <div class="timeline-item"><span>${e.service}</span><span>${e.metric}</span><span>${e.timestamp}</span></div>
    `).join('');

    // 5. Render candidate diagnoses (12 candidates)
    const candHtml = nodes.map((n, idx) => `
        <tr><td>#${idx+1}</td><td>${n.id}</td><td>85%</td><td>${n.type}</td></tr>
    `).join('');

    const t1 = performance.now();
    times.push(t1 - t0);
}

times.sort((a, b) => a - b);
const median = times[Math.floor(times.length / 2)];
const mean = times.reduce((a, b) => a + b, 0) / times.length;
const p95 = times[Math.floor(times.length * 0.95)];

console.log('=== 12-Node Replay Load Benchmark ===');
console.log(`Case: ${caseData.case_id} (${caseData.topology.nodes.length} nodes, ${caseData.topology.edges.length} edges, ${caseData.events.length} events)`);
console.log(`Client Replay Compute & Layout Median: ${median.toFixed(3)} ms`);
console.log(`Client Replay Compute & Layout Mean:   ${mean.toFixed(3)} ms`);
console.log(`Client Replay Compute & Layout p95:    ${p95.toFixed(3)} ms`);
console.log(`Min: ${times[0].toFixed(3)} ms | Max: ${times[times.length - 1].toFixed(3)} ms`);
