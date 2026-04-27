#!/usr/bin/env python3
"""Build an interactive 5-tab visualization page for the knowledge base.

Usage:
  python3 .kb/build-cool-viz.py
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

KB_DIR = Path(__file__).parent
BASE = KB_DIR.parent
PUBLISH = BASE / "publish"
INDEX = KB_DIR / "index" / "_unified"


def load_data():
    metadata = json.loads((INDEX / "metadata.json").read_text())
    graph = json.loads((INDEX / "graph.json").read_text())
    clusters = json.loads((INDEX / "clusters.json").read_text())

    # Build nodes
    nodes = []
    for qslug, meta in metadata.items():
        out = graph["adjacency"].get(qslug, {}).get("outgoing", [])
        inc = graph["adjacency"].get(qslug, {}).get("incoming", [])
        nodes.append({
            "id": qslug,
            "title": meta.get("title", qslug),
            "type": meta.get("type", "unknown"),
            "kb": meta.get("kb", "unknown"),
            "tags": meta.get("tags", [])[:5],
            "created": meta.get("created", ""),
            "words": meta.get("word_count", 0),
            "degree": len(set(out + inc)),
        })

    # Build edges
    edges = []
    seen = set()
    for qslug, adj in graph["adjacency"].items():
        for target in adj.get("outgoing", []):
            key = tuple(sorted([qslug, target]))
            if key not in seen and target in metadata:
                seen.add(key)
                edges.append({"source": qslug, "target": target})

    # Assign cluster colors
    cluster_map = {}
    for cname, cdata in clusters.items():
        for slug in cdata.get("sample_notes", []):
            cluster_map[slug] = cname
    # Also assign by primary tag match
    cluster_tags = {cname: set(cdata.get("tags", [])) for cname, cdata in clusters.items()}
    for n in nodes:
        if n["id"] not in cluster_map:
            for cname, ctags in cluster_tags.items():
                if set(n["tags"]) & ctags:
                    cluster_map[n["id"]] = cname
                    break

    cluster_names = list(clusters.keys())

    return nodes, edges, cluster_map, cluster_names


def build_html(nodes, edges, cluster_map, cluster_names):
    nodes_json = json.dumps(nodes)
    edges_json = json.dumps(edges)
    cluster_map_json = json.dumps(cluster_map)
    cluster_names_json = json.dumps(cluster_names)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Knowledge Base Visualizations</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0a0f; color:#e0e0e0; font-family:'SF Mono',Consolas,monospace; overflow:hidden; height:100vh; }}
.tabs {{ display:flex; background:#111118; border-bottom:1px solid #2a2a3a; z-index:10; position:relative; }}
.tab {{ padding:12px 24px; cursor:pointer; color:#888; font-size:13px; letter-spacing:0.5px; transition:all 0.3s; border-bottom:2px solid transparent; user-select:none; }}
.tab:hover {{ color:#ccc; background:#1a1a25; }}
.tab.active {{ color:#7df; border-bottom-color:#7df; background:#0d0d18; }}
.viz {{ display:none; width:100vw; height:calc(100vh - 47px); position:relative; }}
.viz.active {{ display:block; }}
canvas {{ display:block; }}
.info-panel {{ position:absolute; top:16px; right:16px; background:rgba(10,10,20,0.92); border:1px solid #2a2a4a; border-radius:8px; padding:16px; max-width:320px; font-size:12px; line-height:1.6; pointer-events:none; z-index:5; backdrop-filter:blur(8px); }}
.info-panel h3 {{ color:#7df; margin-bottom:8px; font-size:14px; }}
.info-panel .stat {{ color:#aaa; }}
.info-panel .highlight {{ color:#fda; }}
.controls {{ position:absolute; bottom:16px; left:50%; transform:translateX(-50%); display:flex; gap:8px; z-index:5; }}
.controls button {{ background:#1a1a2e; border:1px solid #3a3a5a; color:#7df; padding:8px 16px; border-radius:4px; cursor:pointer; font-family:inherit; font-size:12px; transition:all 0.2s; }}
.controls button:hover {{ background:#2a2a4e; border-color:#7df; }}
.controls button.active {{ background:#7df; color:#0a0a0f; }}
.tooltip {{ position:absolute; background:rgba(10,10,30,0.95); border:1px solid #4a4a7a; border-radius:6px; padding:10px 14px; font-size:11px; pointer-events:none; z-index:20; max-width:280px; line-height:1.5; display:none; backdrop-filter:blur(8px); }}
.tooltip .tt-title {{ color:#7df; font-size:13px; font-weight:bold; margin-bottom:4px; }}
.tooltip .tt-type {{ color:#fda; font-size:10px; text-transform:uppercase; letter-spacing:1px; }}
.tooltip .tt-tags {{ color:#aaa; margin-top:4px; }}
.speed-label {{ color:#888; font-size:11px; align-self:center; }}
svg {{ width:100%; height:100%; }}
#terrain-canvas {{ width:100%; height:100%; }}
</style>
</head>
<body>
<div class="tabs">
  <div class="tab active" data-tab="galaxy">&#x2726; Knowledge Galaxy</div>
  <div class="tab" data-tab="timelapse">&#x23F1; Time-Lapse Growth</div>
  <div class="tab" data-tab="pulse">&#x26A1; Neural Pulse</div>
  <div class="tab" data-tab="terrain">&#x26F0; Topic Map</div>
  <div class="tab" data-tab="constellation">&#x2B50; Constellations</div>
</div>

<div class="viz active" id="galaxy"></div>
<div class="viz" id="timelapse"><svg id="timelapse-svg"></svg></div>
<div class="viz" id="pulse"><svg id="pulse-svg"></svg></div>
<div class="viz" id="terrain"><canvas id="terrain-canvas"></canvas></div>
<div class="viz" id="constellation"><canvas id="constellation-canvas"></canvas></div>

<div class="tooltip" id="tooltip"></div>

<script>
// ─── Data ───
const NODES = {nodes_json};
const EDGES = {edges_json};
const CLUSTER_MAP = {cluster_map_json};
const CLUSTER_NAMES = {cluster_names_json};

const PALETTE = [
  '#ff6b6b','#4ecdc4','#45b7d1','#96ceb4','#ffeaa7',
  '#dfe6e9','#fd79a8','#6c5ce7','#00b894','#e17055',
  '#74b9ff','#a29bfe','#55efc4','#fdcb6e','#e84393',
  '#00cec9'
];

function clusterColor(id) {{
  const c = CLUSTER_MAP[id];
  const idx = c ? CLUSTER_NAMES.indexOf(c) : -1;
  return idx >= 0 ? PALETTE[idx % PALETTE.length] : '#555';
}}

function clusterColorHex(id) {{
  return parseInt(clusterColor(id).replace('#',''), 16);
}}

const nodeMap = {{}};
NODES.forEach(n => nodeMap[n.id] = n);

// ─── Tabs ───
document.querySelectorAll('.tab').forEach(tab => {{
  tab.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.viz').forEach(v => v.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'galaxy' && !galaxyInit) initGalaxy();
    if (tab.dataset.tab === 'timelapse' && !timelapseInit) initTimelapse();
    if (tab.dataset.tab === 'pulse' && !pulseInit) initPulse();
    if (tab.dataset.tab === 'terrain' && !terrainInit) initTerrain();
    if (tab.dataset.tab === 'constellation' && !constellationInit) initConstellation();
  }});
}});

const tooltip = document.getElementById('tooltip');
function showTooltip(e, node) {{
  tooltip.innerHTML = `<div class="tt-title">${{node.title}}</div>
    <div class="tt-type">${{node.type}} · ${{node.kb}}</div>
    <div class="tt-tags">${{node.tags.join(', ')}}</div>
    <div style="color:#888;margin-top:4px">${{node.words}} words · ${{node.degree}} links</div>`;
  tooltip.style.display = 'block';
  tooltip.style.left = Math.min(e.clientX + 12, window.innerWidth - 300) + 'px';
  tooltip.style.top = (e.clientY + 12) + 'px';
}}
function hideTooltip() {{ tooltip.style.display = 'none'; }}

// ════════════════════════════════════════════════════════════════
// 1. KNOWLEDGE GALAXY (Three.js 3D)
// ════════════════════════════════════════════════════════════════
let galaxyInit = false;
function initGalaxy() {{
  galaxyInit = true;
  const container = document.getElementById('galaxy');
  const W = container.clientWidth, H = container.clientHeight;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0a0a0f, 0.0003);
  const camera = new THREE.PerspectiveCamera(60, W/H, 1, 5000);
  camera.position.set(0, 0, 350);
  const renderer = new THREE.WebGLRenderer({{ antialias:true, alpha:true }});
  renderer.setSize(W, H);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.appendChild(renderer.domElement);

  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.3;

  // Background stars
  const starGeo = new THREE.BufferGeometry();
  const starPos = new Float32Array(3000 * 3);
  for (let i = 0; i < 3000 * 3; i++) starPos[i] = (Math.random() - 0.5) * 4000;
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({{ color:0x6666aa, size:1.5 }})));

  // Layout: spring-like with cluster grouping
  const pos = {{}};
  const vel = {{}};
  NODES.forEach(n => {{
    const ci = CLUSTER_NAMES.indexOf(CLUSTER_MAP[n.id] || '');
    const angle = ci >= 0 ? (ci / CLUSTER_NAMES.length) * Math.PI * 2 : Math.random() * Math.PI * 2;
    const r = 150 + Math.random() * 200;
    pos[n.id] = [
      Math.cos(angle) * r + (Math.random()-0.5)*80,
      (Math.random()-0.5) * 300,
      Math.sin(angle) * r + (Math.random()-0.5)*80
    ];
    vel[n.id] = [0,0,0];
  }});

  // Quick force iterations
  for (let iter = 0; iter < 80; iter++) {{
    // Repulsion
    for (let i = 0; i < NODES.length; i++) {{
      for (let j = i+1; j < NODES.length; j++) {{
        const a = NODES[i].id, b = NODES[j].id;
        const dx = pos[a][0]-pos[b][0], dy = pos[a][1]-pos[b][1], dz = pos[a][2]-pos[b][2];
        const d2 = dx*dx+dy*dy+dz*dz+1;
        if (d2 > 40000) continue;
        const f = 200 / d2;
        vel[a][0]+=dx*f; vel[a][1]+=dy*f; vel[a][2]+=dz*f;
        vel[b][0]-=dx*f; vel[b][1]-=dy*f; vel[b][2]-=dz*f;
      }}
    }}
    // Attraction
    EDGES.forEach(e => {{
      if (!pos[e.source] || !pos[e.target]) return;
      const dx = pos[e.target][0]-pos[e.source][0];
      const dy = pos[e.target][1]-pos[e.source][1];
      const dz = pos[e.target][2]-pos[e.source][2];
      const d = Math.sqrt(dx*dx+dy*dy+dz*dz)+1;
      const f = (d - 30) * 0.01;
      vel[e.source][0]+=dx/d*f; vel[e.source][1]+=dy/d*f; vel[e.source][2]+=dz/d*f;
      vel[e.target][0]-=dx/d*f; vel[e.target][1]-=dy/d*f; vel[e.target][2]-=dz/d*f;
    }});
    // Apply
    Object.keys(pos).forEach(id => {{
      pos[id][0]+=vel[id][0]; pos[id][1]+=vel[id][1]; pos[id][2]+=vel[id][2];
      vel[id][0]*=0.8; vel[id][1]*=0.8; vel[id][2]*=0.8;
    }});
  }}

  // Node particles
  const nodeGeo = new THREE.BufferGeometry();
  const positions = new Float32Array(NODES.length * 3);
  const colors = new Float32Array(NODES.length * 3);
  const sizes = new Float32Array(NODES.length);
  NODES.forEach((n, i) => {{
    const p = pos[n.id];
    positions[i*3] = p[0]; positions[i*3+1] = p[1]; positions[i*3+2] = p[2];
    const c = new THREE.Color(clusterColorHex(n.id));
    colors[i*3] = c.r; colors[i*3+1] = c.g; colors[i*3+2] = c.b;
    sizes[i] = 8 + Math.min(n.degree, 20) * 1.5;
  }});
  nodeGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  nodeGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  nodeGeo.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

  const nodeMat = new THREE.ShaderMaterial({{
    uniforms: {{ time: {{ value: 0 }} }},
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    vertexShader: `
      attribute float size;
      varying vec3 vColor;
      uniform float time;
      void main() {{
        vColor = color;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = size * (400.0 / -mv.z) * (1.0 + 0.15 * sin(time * 2.0 + position.x * 0.01));
        gl_Position = projectionMatrix * mv;
      }}
    `,
    fragmentShader: `
      varying vec3 vColor;
      void main() {{
        float d = length(gl_PointCoord - 0.5);
        if (d > 0.5) discard;
        float glow = exp(-d * 2.5);
        float core = smoothstep(0.5, 0.1, d);
        gl_FragColor = vec4(vColor * (0.5 + core * 0.5) + core * 0.3, glow);
      }}
    `
  }});
  scene.add(new THREE.Points(nodeGeo, nodeMat));

  // Edges as lines
  const edgeGeo = new THREE.BufferGeometry();
  const edgePos = [];
  const edgeCol = [];
  EDGES.forEach(e => {{
    if (!pos[e.source] || !pos[e.target]) return;
    const cs = new THREE.Color(clusterColorHex(e.source));
    const ct = new THREE.Color(clusterColorHex(e.target));
    edgePos.push(...pos[e.source], ...pos[e.target]);
    edgeCol.push(cs.r, cs.g, cs.b, ct.r, ct.g, ct.b);
  }});
  edgeGeo.setAttribute('position', new THREE.Float32BufferAttribute(edgePos, 3));
  edgeGeo.setAttribute('color', new THREE.Float32BufferAttribute(edgeCol, 3));
  const edgeMat = new THREE.LineBasicMaterial({{ vertexColors:true, transparent:true, opacity:0.25 }});
  scene.add(new THREE.LineSegments(edgeGeo, edgeMat));

  // Nebula blobs for clusters
  const nebulaGeo = new THREE.BufferGeometry();
  const nebulaPos = [];
  const nebulaCol = [];
  const nebulaSizes = [];
  CLUSTER_NAMES.forEach((cname, ci) => {{
    const members = NODES.filter(n => CLUSTER_MAP[n.id] === cname);
    if (!members.length) return;
    let cx=0,cy=0,cz=0;
    members.forEach(m => {{ cx+=pos[m.id][0]; cy+=pos[m.id][1]; cz+=pos[m.id][2]; }});
    cx/=members.length; cy/=members.length; cz/=members.length;
    for (let i = 0; i < 40; i++) {{
      nebulaPos.push(cx+(Math.random()-0.5)*120, cy+(Math.random()-0.5)*120, cz+(Math.random()-0.5)*120);
      const c = new THREE.Color(PALETTE[ci % PALETTE.length]);
      nebulaCol.push(c.r, c.g, c.b);
      nebulaSizes.push(50 + Math.random() * 60);
    }}
  }});
  if (nebulaPos.length) {{
    nebulaGeo.setAttribute('position', new THREE.Float32BufferAttribute(nebulaPos, 3));
    nebulaGeo.setAttribute('color', new THREE.Float32BufferAttribute(nebulaCol, 3));
    nebulaGeo.setAttribute('size', new THREE.Float32BufferAttribute(nebulaSizes, 1));
    const nebulaMat = new THREE.ShaderMaterial({{
      vertexColors: true, transparent: true, depthWrite: false,
      vertexShader: `
        attribute float size;
        varying vec3 vColor;
        void main() {{
          vColor = color;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = size * (300.0 / -mv.z);
          gl_Position = projectionMatrix * mv;
        }}
      `,
      fragmentShader: `
        varying vec3 vColor;
        void main() {{
          float d = length(gl_PointCoord - 0.5);
          float a = exp(-d * 2.0) * 0.12;
          gl_FragColor = vec4(vColor, a);
        }}
      `
    }});
    scene.add(new THREE.Points(nebulaGeo, nebulaMat));
  }}

  // ── Build adjacency for pulse routing ──
  const galAdj = {{}};
  EDGES.forEach(e => {{
    if (!pos[e.source] || !pos[e.target]) return;
    if (!galAdj[e.source]) galAdj[e.source] = [];
    if (!galAdj[e.target]) galAdj[e.target] = [];
    galAdj[e.source].push(e.target);
    galAdj[e.target].push(e.source);
  }});

  // ── Edge index for highlight (maps edge key -> index in edgePos) ──
  const edgeIndex = {{}};
  let eIdx = 0;
  EDGES.forEach(e => {{
    if (!pos[e.source] || !pos[e.target]) return;
    edgeIndex[e.source + '|' + e.target] = eIdx;
    edgeIndex[e.target + '|' + e.source] = eIdx;
    eIdx++;
  }});

  // ── Floating HTML labels for ALL nodes (distance-based visibility) ──
  const labelContainer = document.createElement('div');
  labelContainer.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;overflow:hidden;';
  container.appendChild(labelContainer);

  const labels = {{}};
  const labelNodes = NODES; // all nodes get labels
  labelNodes.forEach(n => {{
    const el = document.createElement('div');
    el.style.cssText = 'position:absolute;color:#fff;font-size:10px;white-space:nowrap;opacity:0;text-shadow:0 0 8px rgba(0,0,0,1),0 0 3px rgba(0,0,0,1);transform:translate(-50%,-100%);padding:2px 6px;display:none;';
    el.textContent = n.title.length > 35 ? n.title.slice(0,33) + '...' : n.title;
    labelContainer.appendChild(el);
    labels[n.id] = el;
  }});
  // Track which labels are force-shown by hover
  let hoverForceLabels = new Set();

  // ── Detail panel (shows on hover) ──
  const detailPanel = document.createElement('div');
  detailPanel.style.cssText = 'position:absolute;top:16px;left:16px;background:rgba(10,10,25,0.94);border:1px solid #3a3a6a;border-radius:10px;padding:20px;max-width:340px;font-size:12px;line-height:1.7;z-index:5;backdrop-filter:blur(10px);display:none;pointer-events:none;';
  container.appendChild(detailPanel);

  // Info panel (top right)
  const info = document.createElement('div');
  info.className = 'info-panel';
  info.innerHTML = `<h3>Knowledge Galaxy</h3>
    <div class="stat">${{NODES.length}} notes &middot; ${{EDGES.length}} connections</div>
    <div class="stat">${{CLUSTER_NAMES.length}} clusters</div>
    <div style="margin-top:8px;color:#666">Hover a node to explore its connections</div>
    <div style="color:#666">Drag to rotate &middot; Scroll to zoom</div>`;
  container.appendChild(info);

  // ── Pulse system ──
  const pulseGroup = new THREE.Group();
  scene.add(pulseGroup);
  const pulsePool = [];
  const activePulses = [];

  function createPulseMesh() {{
    const geo = new THREE.SphereGeometry(1.5, 8, 8);
    const mat = new THREE.MeshBasicMaterial({{ color: 0xffffff, transparent: true, opacity: 1 }});
    return new THREE.Mesh(geo, mat);
  }}

  function spawnPulse(fromId, toId, color) {{
    let mesh = pulsePool.pop();
    if (!mesh) mesh = createPulseMesh();
    mesh.material.color.set(color);
    mesh.material.opacity = 1;
    mesh.position.set(...pos[fromId]);
    pulseGroup.add(mesh);
    activePulses.push({{
      mesh,
      from: pos[fromId],
      to: pos[toId],
      progress: 0,
      speed: 0.015 + Math.random() * 0.01
    }});
  }}

  function updatePulses() {{
    for (let i = activePulses.length - 1; i >= 0; i--) {{
      const p = activePulses[i];
      p.progress += p.speed;
      if (p.progress >= 1) {{
        pulseGroup.remove(p.mesh);
        pulsePool.push(p.mesh);
        activePulses.splice(i, 1);
        continue;
      }}
      const t = p.progress;
      p.mesh.position.set(
        p.from[0] + (p.to[0] - p.from[0]) * t,
        p.from[1] + (p.to[1] - p.from[1]) * t,
        p.from[2] + (p.to[2] - p.from[2]) * t
      );
      p.mesh.material.opacity = t < 0.2 ? t * 5 : (1 - t) * 1.25;
      const scale = 1 + Math.sin(t * Math.PI) * 1.5;
      p.mesh.scale.set(scale, scale, scale);
    }}
  }}

  // ── Hover state ──
  let hoveredNode = null;
  let pulseInterval = null;
  const origEdgeColors = new Float32Array(edgeCol.length);
  origEdgeColors.set(edgeGeo.attributes.color.array);
  const origNodeColors = new Float32Array(colors.length);
  origNodeColors.set(nodeGeo.attributes.color.array);
  const origNodeSizes = new Float32Array(sizes.length);
  origNodeSizes.set(nodeGeo.attributes.size.array);

  function setHover(nodeData) {{
    if (hoveredNode === nodeData) return;
    hoveredNode = nodeData;

    if (pulseInterval) {{ clearInterval(pulseInterval); pulseInterval = null; }}
    // Clear active pulses
    activePulses.forEach(p => pulseGroup.remove(p.mesh));
    activePulses.length = 0;

    if (!nodeData) {{
      // Restore everything
      nodeGeo.attributes.color.array.set(origNodeColors);
      nodeGeo.attributes.color.needsUpdate = true;
      nodeGeo.attributes.size.array.set(origNodeSizes);
      nodeGeo.attributes.size.needsUpdate = true;
      edgeGeo.attributes.color.array.set(origEdgeColors);
      edgeGeo.attributes.color.needsUpdate = true;
      edgeMat.opacity = 0.25;
      detailPanel.style.display = 'none';
      hoverForceLabels = new Set();
      return;
    }}

    const neighbors = new Set(galAdj[nodeData.id] || []);
    const hColor = new THREE.Color(clusterColorHex(nodeData.id));

    // Dim non-neighbors, brighten neighbors
    NODES.forEach((n, i) => {{
      if (n.id === nodeData.id) {{
        // Hovered node: bright white core
        nodeGeo.attributes.color.array[i*3] = 1;
        nodeGeo.attributes.color.array[i*3+1] = 1;
        nodeGeo.attributes.color.array[i*3+2] = 1;
        nodeGeo.attributes.size.array[i] = origNodeSizes[i] * 2;
      }} else if (neighbors.has(n.id)) {{
        // Neighbor: full color, slightly bigger
        nodeGeo.attributes.color.array[i*3] = origNodeColors[i*3];
        nodeGeo.attributes.color.array[i*3+1] = origNodeColors[i*3+1];
        nodeGeo.attributes.color.array[i*3+2] = origNodeColors[i*3+2];
        nodeGeo.attributes.size.array[i] = origNodeSizes[i] * 1.3;
      }} else {{
        // Non-neighbor: very dim
        nodeGeo.attributes.color.array[i*3] = origNodeColors[i*3] * 0.15;
        nodeGeo.attributes.color.array[i*3+1] = origNodeColors[i*3+1] * 0.15;
        nodeGeo.attributes.color.array[i*3+2] = origNodeColors[i*3+2] * 0.15;
        nodeGeo.attributes.size.array[i] = origNodeSizes[i] * 0.5;
      }}
    }});
    nodeGeo.attributes.color.needsUpdate = true;
    nodeGeo.attributes.size.needsUpdate = true;

    // Highlight connected edges, dim the rest
    const edgeColors = edgeGeo.attributes.color.array;
    let ei = 0;
    EDGES.forEach(e => {{
      if (!pos[e.source] || !pos[e.target]) return;
      const connected = (e.source === nodeData.id || e.target === nodeData.id);
      if (connected) {{
        edgeColors[ei*6] = hColor.r; edgeColors[ei*6+1] = hColor.g; edgeColors[ei*6+2] = hColor.b;
        edgeColors[ei*6+3] = hColor.r; edgeColors[ei*6+4] = hColor.g; edgeColors[ei*6+5] = hColor.b;
      }} else {{
        for (let k = 0; k < 6; k++) edgeColors[ei*6+k] = origEdgeColors[ei*6+k] * 0.08;
      }}
      ei++;
    }});
    edgeGeo.attributes.color.needsUpdate = true;
    edgeMat.opacity = 0.6;

    // Force-show labels for hovered node + neighbors
    hoverForceLabels = new Set([nodeData.id, ...neighbors]);

    // Detail panel
    const neighborNames = [...neighbors].slice(0, 8).map(id => {{
      const n = nodeMap[id];
      return n ? n.title : id.split(':').pop();
    }});
    detailPanel.innerHTML = `
      <div style="color:${{clusterColor(nodeData.id)}};font-size:16px;font-weight:bold;margin-bottom:6px">${{nodeData.title}}</div>
      <div style="color:#fda;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">${{nodeData.type}} &middot; ${{nodeData.kb}}</div>
      <div style="color:#aaa;margin-bottom:8px">${{nodeData.tags.join(', ')}}</div>
      <div style="color:#888;margin-bottom:10px">${{nodeData.words}} words &middot; ${{nodeData.degree}} connections</div>
      <div style="color:#7df;font-size:11px;margin-bottom:4px">Connected to:</div>
      <div style="color:#ccc;font-size:11px">${{neighborNames.map(n => '&bull; ' + n).join('<br>')}}</div>
      ${{neighbors.size > 8 ? '<div style="color:#666;font-size:10px;margin-top:4px">+ ' + (neighbors.size - 8) + ' more...</div>' : ''}}
    `;
    detailPanel.style.display = 'block';

    // Send pulses periodically
    function firePulses() {{
      const targets = galAdj[nodeData.id] || [];
      targets.forEach(t => {{
        if (Math.random() < 0.4) return; // stagger them
        spawnPulse(nodeData.id, t, clusterColorHex(nodeData.id));
      }});
    }}
    firePulses();
    pulseInterval = setInterval(firePulses, 500);
  }}

  // ── Raycaster ──
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = 8;
  const mouse = new THREE.Vector2();
  let lastHoverCheck = 0;
  renderer.domElement.addEventListener('mousemove', e => {{
    const now = performance.now();
    if (now - lastHoverCheck < 50) return; // throttle
    lastHoverCheck = now;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObjects(scene.children);
    const ptHit = hits.find(h => h.object.geometry === nodeGeo);
    if (ptHit && NODES[ptHit.index]) {{
      setHover(NODES[ptHit.index]);
    }} else {{
      setHover(null);
    }}
  }});
  renderer.domElement.addEventListener('mouseleave', () => setHover(null));

  // ── Project labels to 2D each frame (distance-based + hover) ──
  const tempV = new THREE.Vector3();
  const camPos = new THREE.Vector3();
  function updateLabels() {{
    camPos.copy(camera.position);
    const isHovering = hoveredNode !== null;

    labelNodes.forEach(n => {{
      const p = pos[n.id];
      const el = labels[n.id];

      // Distance from camera to this node (in world space)
      tempV.set(p[0], p[1], p[2]);
      const distToCamera = camPos.distanceTo(tempV);

      // Project to screen
      tempV.project(camera);
      const x = (tempV.x * 0.5 + 0.5) * W;
      const y = (-tempV.y * 0.5 + 0.5) * H;

      // Behind camera
      if (tempV.z > 1) {{ el.style.display = 'none'; return; }}

      // Determine visibility
      const forceShown = hoverForceLabels.has(n.id);

      // Distance thresholds: closer = more labels visible
      // High-degree nodes show from farther away
      const degreeBonus = Math.min(n.degree, 20) * 8;
      const showDist = 120 + degreeBonus; // base 120, up to 280 for high-degree

      let targetOpacity = 0;
      if (forceShown) {{
        // Hover-forced: full opacity for hovered/neighbors
        targetOpacity = (n.id === (hoveredNode && hoveredNode.id)) ? 1.0 : 0.85;
      }} else if (isHovering) {{
        // Hovering but not in neighborhood: hide
        targetOpacity = 0;
      }} else if (distToCamera < showDist) {{
        // Distance-based fade: closer = more opaque
        targetOpacity = Math.max(0, 1 - (distToCamera / showDist)) * 0.8;
      }}

      if (targetOpacity < 0.05) {{
        el.style.display = 'none';
        return;
      }}

      el.style.display = 'block';
      el.style.left = x + 'px';
      el.style.top = (y - 8) + 'px';
      el.style.opacity = targetOpacity.toFixed(2);

      // Scale label size based on distance
      const fontSize = forceShown ? 12 : Math.max(9, Math.min(13, 200 / distToCamera * 10));
      el.style.fontSize = fontSize.toFixed(0) + 'px';
    }});
  }}

  // ── Animate ──
  let t = 0;
  function animate() {{
    requestAnimationFrame(animate);
    t += 0.016;
    nodeMat.uniforms.time.value = t;
    controls.update();
    updatePulses();
    updateLabels();
    renderer.render(scene, camera);
  }}
  animate();

  window.addEventListener('resize', () => {{
    const w2 = container.clientWidth, h2 = container.clientHeight;
    camera.aspect = w2/h2;
    camera.updateProjectionMatrix();
    renderer.setSize(w2, h2);
  }});
}}

// ════════════════════════════════════════════════════════════════
// 2. TIME-LAPSE GROWTH (D3 animated)
// ════════════════════════════════════════════════════════════════
let timelapseInit = false;
function initTimelapse() {{
  timelapseInit = true;
  const container = document.getElementById('timelapse');
  const W = container.clientWidth, H = container.clientHeight;
  const svg = d3.select('#timelapse-svg').attr('width', W).attr('height', H);

  // Sort nodes by date
  const sorted = [...NODES].filter(n => n.created).sort((a,b) => a.created.localeCompare(b.created));
  const dates = [...new Set(sorted.map(n => n.created))].sort();
  const edgeSet = new Map();
  EDGES.forEach(e => {{
    if (!edgeSet.has(e.source)) edgeSet.set(e.source, []);
    edgeSet.get(e.source).push(e.target);
    if (!edgeSet.has(e.target)) edgeSet.set(e.target, []);
    edgeSet.get(e.target).push(e.source);
  }});

  const sim = d3.forceSimulation()
    .force('charge', d3.forceManyBody().strength(-40).distanceMax(200))
    .force('center', d3.forceCenter(W/2, H/2))
    .force('collision', d3.forceCollide(8))
    .force('link', d3.forceLink().id(d => d.id).distance(40).strength(0.3))
    .alphaDecay(0.01)
    .on('tick', ticked);

  const linkG = svg.append('g');
  const nodeG = svg.append('g');
  let activeNodes = [];
  let activeEdges = [];
  let step = 0;
  let playing = true;
  let speed = 200;

  // Date label
  const dateLabel = svg.append('text')
    .attr('x', W/2).attr('y', 50)
    .attr('text-anchor', 'middle')
    .attr('fill', '#7df').attr('font-size', '28px')
    .attr('font-family', 'inherit').attr('font-weight', 'bold');

  const countLabel = svg.append('text')
    .attr('x', W/2).attr('y', 80)
    .attr('text-anchor', 'middle')
    .attr('fill', '#888').attr('font-size', '14px')
    .attr('font-family', 'inherit');

  function ticked() {{
    linkG.selectAll('line')
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeG.selectAll('circle')
      .attr('cx', d => d.x).attr('cy', d => d.y);
  }}

  function addDay() {{
    if (step >= dates.length) {{ playing = false; return; }}
    const day = dates[step];
    const newNodes = sorted.filter(n => n.created === day);
    activeNodes.push(...newNodes);
    const activeIds = new Set(activeNodes.map(n => n.id));

    // Add new edges
    newNodes.forEach(n => {{
      (edgeSet.get(n.id) || []).forEach(t => {{
        if (activeIds.has(t) && !activeEdges.find(e => (e.source.id||e.source)===n.id && (e.target.id||e.target)===t || (e.source.id||e.source)===t && (e.target.id||e.target)===n.id))
          activeEdges.push({{source: n.id, target: t}});
      }});
    }});

    sim.nodes(activeNodes);
    sim.force('link').links(activeEdges);
    sim.alpha(0.5).restart();

    // Render links
    const links = linkG.selectAll('line').data(activeEdges, d => (d.source.id||d.source)+'-'+(d.target.id||d.target));
    links.enter().append('line')
      .attr('stroke', '#2a2a4a').attr('stroke-width', 0.5)
      .attr('stroke-opacity', 0)
      .transition().duration(400).attr('stroke-opacity', 0.3);

    // Render nodes
    const circles = nodeG.selectAll('circle').data(activeNodes, d => d.id);
    circles.enter().append('circle')
      .attr('r', 0)
      .attr('fill', d => clusterColor(d.id))
      .attr('cx', W/2).attr('cy', H/2)
      .on('mouseover', (e, d) => showTooltip(e, d))
      .on('mouseout', hideTooltip)
      .call(d3.drag()
        .on('start', (e,d) => {{ if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
        .on('drag', (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
        .on('end', (e,d) => {{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }})
      )
      .transition().duration(600).ease(d3.easeElasticOut)
      .attr('r', d => 3 + Math.min(d.degree, 15) * 0.5);

    dateLabel.text(day);
    countLabel.text(`${{activeNodes.length}} / ${{NODES.length}} notes`);
    step++;
  }}

  let timer = null;
  function play() {{
    if (timer) return;
    playing = true;
    timer = setInterval(() => {{
      if (!playing || step >= dates.length) {{ clearInterval(timer); timer = null; return; }}
      addDay();
    }}, speed);
  }}

  // Controls
  const ctrl = document.createElement('div');
  ctrl.className = 'controls';
  ctrl.innerHTML = `
    <button id="tl-play" class="active">&#x25B6; Play</button>
    <button id="tl-pause">&#x23F8; Pause</button>
    <button id="tl-reset">&#x21BA; Reset</button>
    <span class="speed-label">Speed:</span>
    <button id="tl-slow">0.5x</button>
    <button id="tl-normal" class="active">1x</button>
    <button id="tl-fast">3x</button>
  `;
  container.appendChild(ctrl);

  document.getElementById('tl-play').onclick = () => {{ playing = true; play(); }};
  document.getElementById('tl-pause').onclick = () => {{ playing = false; if(timer){{ clearInterval(timer); timer=null; }} }};
  document.getElementById('tl-reset').onclick = () => {{
    playing = false; if(timer){{ clearInterval(timer); timer=null; }}
    step = 0; activeNodes = []; activeEdges = [];
    linkG.selectAll('line').remove();
    nodeG.selectAll('circle').remove();
    sim.nodes([]); sim.force('link').links([]);
    dateLabel.text(''); countLabel.text('');
  }};
  document.getElementById('tl-slow').onclick = () => {{ speed = 400; if(timer){{ clearInterval(timer); timer=null; play(); }} }};
  document.getElementById('tl-normal').onclick = () => {{ speed = 200; if(timer){{ clearInterval(timer); timer=null; play(); }} }};
  document.getElementById('tl-fast').onclick = () => {{ speed = 60; if(timer){{ clearInterval(timer); timer=null; play(); }} }};

  play();
}}

// ════════════════════════════════════════════════════════════════
// 3. NEURAL PULSE NETWORK (D3 + animated pulses)
// ════════════════════════════════════════════════════════════════
let pulseInit = false;
function initPulse() {{
  pulseInit = true;
  const container = document.getElementById('pulse');
  const W = container.clientWidth, H = container.clientHeight;
  const svg = d3.select('#pulse-svg').attr('width', W).attr('height', H);

  // Use top nodes by degree for clarity
  const topN = [...NODES].sort((a,b) => b.degree - a.degree).slice(0, 150);
  const topIds = new Set(topN.map(n => n.id));
  const topEdges = EDGES.filter(e => topIds.has(e.source) && topIds.has(e.target));

  // Adjacency for highlight
  const adj = {{}};
  topEdges.forEach(e => {{
    if (!adj[e.source]) adj[e.source] = new Set();
    if (!adj[e.target]) adj[e.target] = new Set();
    adj[e.source].add(e.target);
    adj[e.target].add(e.source);
  }});

  const sim = d3.forceSimulation(topN)
    .force('link', d3.forceLink(topEdges).id(d => d.id).distance(50).strength(0.4))
    .force('charge', d3.forceManyBody().strength(-60).distanceMax(250))
    .force('center', d3.forceCenter(W/2, H/2))
    .force('collision', d3.forceCollide(10));

  // Glow filter
  const defs = svg.append('defs');
  const filter = defs.append('filter').attr('id', 'glow');
  filter.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'blur');
  filter.append('feMerge').selectAll('feMergeNode')
    .data(['blur', 'SourceGraphic']).enter().append('feMergeNode')
    .attr('in', d => d);

  const linkG = svg.append('g');
  const pulseG = svg.append('g');
  const nodeG = svg.append('g');

  const links = linkG.selectAll('line').data(topEdges).enter().append('line')
    .attr('stroke', '#1a1a3a').attr('stroke-width', 0.8);

  const nodes = nodeG.selectAll('circle').data(topN).enter().append('circle')
    .attr('r', d => 4 + Math.min(d.degree, 20) * 0.4)
    .attr('fill', d => clusterColor(d.id))
    .attr('filter', 'url(#glow)')
    .attr('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (e,d) => {{ if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
      .on('drag', (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
      .on('end', (e,d) => {{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }})
    );

  sim.on('tick', () => {{
    links.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
         .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
    nodes.attr('cx', d=>d.x).attr('cy', d=>d.y);
  }});

  // Pulse animation on hover
  let pulseTimer = null;
  nodes.on('mouseover', function(event, d) {{
    showTooltip(event, d);
    // Highlight neighborhood
    const neighbors = adj[d.id] || new Set();
    nodes.attr('opacity', n => n.id === d.id || neighbors.has(n.id) ? 1 : 0.1);
    links.attr('stroke', l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      return (sid===d.id||tid===d.id) ? clusterColor(d.id) : '#0a0a15';
    }}).attr('stroke-width', l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      return (sid===d.id||tid===d.id) ? 2 : 0.3;
    }}).attr('stroke-opacity', l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      return (sid===d.id||tid===d.id) ? 0.8 : 0.3;
    }});

    // Send pulses
    if (pulseTimer) clearInterval(pulseTimer);
    function sendPulse() {{
      const activeEdges = topEdges.filter(e => (e.source.id||e.source)===d.id || (e.target.id||e.target)===d.id);
      activeEdges.forEach(e => {{
        const fromD = (e.source.id||e.source) === d.id;
        const sx = fromD ? e.source.x : e.target.x;
        const sy = fromD ? e.source.y : e.target.y;
        const tx = fromD ? e.target.x : e.source.x;
        const ty = fromD ? e.target.y : e.source.y;
        const pulse = pulseG.append('circle')
          .attr('r', 3).attr('fill', clusterColor(d.id))
          .attr('filter', 'url(#glow)')
          .attr('cx', sx).attr('cy', sy);
        pulse.transition().duration(800).ease(d3.easeQuadOut)
          .attr('cx', tx).attr('cy', ty)
          .attr('r', 5).attr('opacity', 0)
          .remove();
      }});
    }}
    sendPulse();
    pulseTimer = setInterval(sendPulse, 600);
  }}).on('mouseout', function() {{
    hideTooltip();
    if (pulseTimer) {{ clearInterval(pulseTimer); pulseTimer = null; }}
    nodes.attr('opacity', 1);
    links.attr('stroke', '#1a1a3a').attr('stroke-width', 0.8).attr('stroke-opacity', 1);
    pulseG.selectAll('circle').remove();
  }});

  // Info
  const info = document.createElement('div');
  info.className = 'info-panel';
  info.innerHTML = `<h3>Neural Pulse Network</h3>
    <div class="stat">Top ${{topN.length}} nodes by connectivity</div>
    <div class="stat">${{topEdges.length}} connections</div>
    <div style="margin-top:8px;color:#666">Hover a node to see information flow</div>`;
  container.appendChild(info);
}}

// ════════════════════════════════════════════════════════════════
// 4. TOPIC MAP (D3 packed bubble chart)
// ════════════════════════════════════════════════════════════════
let terrainInit = false;
function initTerrain() {{
  terrainInit = true;
  const container = document.getElementById('terrain');
  const W = container.clientWidth, H = container.clientHeight;

  // Replace canvas with SVG
  const canvas = document.getElementById('terrain-canvas');
  canvas.style.display = 'none';
  const svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H)
    .style('width', '100%').style('height', '100%');

  // Build hierarchical data: root -> clusters -> notes
  const clusterData = CLUSTER_NAMES.map((cname, ci) => {{
    const members = NODES.filter(n => CLUSTER_MAP[n.id] === cname);
    return {{
      name: cname,
      color: PALETTE[ci % PALETTE.length],
      children: members.map(m => ({{
        name: m.title,
        id: m.id,
        type: m.type,
        kb: m.kb,
        tags: m.tags,
        words: m.words,
        degree: m.degree,
        value: Math.max(m.words, 200) // size by word count
      }}))
    }};
  }}).filter(c => c.children.length > 0);

  // Add unclustered nodes
  const clustered = new Set();
  clusterData.forEach(c => c.children.forEach(ch => clustered.add(ch.id)));
  const unclustered = NODES.filter(n => !clustered.has(n.id));
  if (unclustered.length) {{
    clusterData.push({{
      name: 'other',
      color: '#555',
      children: unclustered.map(m => ({{
        name: m.title, id: m.id, type: m.type, kb: m.kb,
        tags: m.tags, words: m.words, degree: m.degree,
        value: Math.max(m.words, 200)
      }}))
    }});
  }}

  const root = d3.hierarchy({{ name: 'KB', children: clusterData }})
    .sum(d => d.value || 0)
    .sort((a, b) => b.value - a.value);

  const pack = d3.pack()
    .size([W - 40, H - 40])
    .padding(d => d.depth === 0 ? 20 : 3);

  pack(root);

  // Offset to center
  const ox = 20, oy = 20;

  // State for zoom
  let focus = root;
  let view = [root.x, root.y, root.r * 2];

  function zoomTo(v) {{
    const k = Math.min(W, H) / v[2];
    view = v;
    clusterCircles.attr('transform', d => `translate(${{(d.x - v[0]) * k + W/2}},${{(d.y - v[1]) * k + H/2}})`)
      .select('circle').attr('r', d => d.r * k);
    noteCircles.attr('transform', d => `translate(${{(d.x - v[0]) * k + W/2}},${{(d.y - v[1]) * k + H/2}})`)
      .select('circle').attr('r', d => d.r * k);
    // Update labels
    clusterLabels.attr('x', d => (d.x - v[0]) * k + W/2)
      .attr('y', d => (d.y - v[1]) * k + H/2)
      .style('font-size', d => Math.max(10, Math.min(18, d.r * k * 0.12)) + 'px')
      .style('display', d => d.r * k > 40 ? 'block' : 'none');
    noteLabels.attr('x', d => (d.x - v[0]) * k + W/2)
      .attr('y', d => (d.y - v[1]) * k + H/2 + 1)
      .style('font-size', d => Math.max(7, Math.min(11, d.r * k * 0.35)) + 'px')
      .style('display', d => d.r * k > 20 ? 'block' : 'none');
  }}

  function zoom(d) {{
    focus = d;
    svg.transition().duration(750).tween('zoom', () => {{
      const i = d3.interpolateZoom(view, [d.x, d.y, d.r * 2]);
      return t => zoomTo(i(t));
    }});
  }}

  // Draw cluster circles (depth 1)
  const clusters = root.children || [];
  const clusterCircles = svg.selectAll('.cluster-g')
    .data(clusters).enter().append('g').attr('class', 'cluster-g')
    .style('cursor', 'pointer')
    .on('click', (e, d) => {{ if (focus !== d) {{ zoom(d); e.stopPropagation(); }} }});

  clusterCircles.append('circle')
    .attr('fill', d => d.data.color)
    .attr('fill-opacity', 0.12)
    .attr('stroke', d => d.data.color)
    .attr('stroke-width', 1.5)
    .attr('stroke-opacity', 0.4);

  // Draw note circles (depth 2 = leaves)
  const leaves = root.leaves();
  const noteCircles = svg.selectAll('.note-g')
    .data(leaves).enter().append('g').attr('class', 'note-g')
    .on('mouseover', (e, d) => {{
      showTooltip(e, {{...d.data, title: d.data.name}});
      d3.select(e.currentTarget).select('circle')
        .attr('stroke', '#fff').attr('stroke-width', 2);
    }})
    .on('mouseout', (e, d) => {{
      hideTooltip();
      d3.select(e.currentTarget).select('circle')
        .attr('stroke', 'none');
    }});

  noteCircles.append('circle')
    .attr('fill', d => {{
      const pc = d.parent && d.parent.data.color;
      return pc || '#555';
    }})
    .attr('fill-opacity', d => 0.4 + Math.min(d.data.degree, 15) / 15 * 0.5)
    .attr('stroke', 'none');

  // Cluster labels
  const clusterLabels = svg.selectAll('.cluster-label')
    .data(clusters).enter().append('text')
    .attr('class', 'cluster-label')
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .attr('fill', d => d.data.color)
    .attr('font-family', 'inherit')
    .attr('font-weight', 'bold')
    .attr('pointer-events', 'none')
    .attr('opacity', 0.9)
    .text(d => d.data.name.toUpperCase() + ` (${{d.children ? d.children.length : 0}})`);

  // Note labels
  const noteLabels = svg.selectAll('.note-label')
    .data(leaves).enter().append('text')
    .attr('class', 'note-label')
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .attr('fill', '#ddd')
    .attr('font-family', 'inherit')
    .attr('pointer-events', 'none')
    .attr('opacity', 0.8)
    .text(d => {{
      const t = d.data.name;
      return t.length > 25 ? t.slice(0,23) + '..' : t;
    }});

  // Click background to zoom out
  svg.on('click', () => zoom(root));

  // Initial layout
  zoomTo([root.x, root.y, root.r * 2]);

  // Info
  const info = document.createElement('div');
  info.className = 'info-panel';
  info.innerHTML = `<h3>Topic Map</h3>
    <div class="stat">${{clusters.length}} clusters &middot; ${{leaves.length}} notes</div>
    <div class="stat highlight">Size = word count</div>
    <div class="stat">Opacity = connectivity</div>
    <div style="margin-top:8px;color:#666">Click a cluster to zoom in</div>
    <div style="color:#666">Click background to zoom out</div>`;
  container.appendChild(info);
}}

// ════════════════════════════════════════════════════════════════
// 5. CONSTELLATION VIEW (Canvas)
// ════════════════════════════════════════════════════════════════
let constellationInit = false;
function initConstellation() {{
  constellationInit = true;
  const container = document.getElementById('constellation');
  const canvas = document.getElementById('constellation-canvas');
  const W = container.clientWidth, H = container.clientHeight;
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d');

  // Background: deep space with twinkling
  const bgStars = Array.from({{length: 400}}, () => ({{
    x: Math.random()*W, y: Math.random()*H,
    r: Math.random()*1.5, twinkle: Math.random()*Math.PI*2, speed: 0.5+Math.random()*2
  }}));

  // Layout clusters as constellations
  const constellations = [];
  CLUSTER_NAMES.forEach((cname, ci) => {{
    const members = NODES.filter(n => CLUSTER_MAP[n.id] === cname);
    if (members.length < 3) return;

    // Position constellation
    const angle = (ci / CLUSTER_NAMES.length) * Math.PI * 2;
    const cx = W/2 + Math.cos(angle) * (Math.min(W,H)*0.32);
    const cy = H/2 + Math.sin(angle) * (Math.min(W,H)*0.32);

    const stars = members.slice(0, 20).map((m, i) => {{
      const a = (i / Math.min(members.length, 20)) * Math.PI * 2;
      const r = 30 + Math.random() * 60;
      return {{
        ...m,
        sx: cx + Math.cos(a) * r + (Math.random()-0.5)*20,
        sy: cy + Math.sin(a) * r + (Math.random()-0.5)*20,
        brightness: 0.4 + Math.min(m.degree, 15) / 15 * 0.6,
        size: 1.5 + Math.min(m.degree, 15) * 0.2,
        twinkle: Math.random() * Math.PI * 2
      }};
    }});

    // Build constellation lines (minimum spanning tree of the stars)
    const lines = [];
    if (stars.length > 1) {{
      const connected = new Set([0]);
      while (connected.size < stars.length) {{
        let bestDist = Infinity, bestI = -1, bestJ = -1;
        connected.forEach(i => {{
          stars.forEach((s, j) => {{
            if (connected.has(j)) return;
            const d = Math.hypot(stars[i].sx - s.sx, stars[i].sy - s.sy);
            if (d < bestDist) {{ bestDist = d; bestI = i; bestJ = j; }}
          }});
        }});
        if (bestJ >= 0) {{
          connected.add(bestJ);
          lines.push([bestI, bestJ]);
        }} else break;
      }}
    }}

    constellations.push({{ name: cname, color: PALETTE[ci % PALETTE.length], stars, lines, cx, cy, count: members.length }});
  }});

  let hovered = null;
  let time = 0;

  function draw() {{
    ctx.clearRect(0, 0, W, H);

    // Background gradient
    const grad = ctx.createRadialGradient(W/2, H/2, 0, W/2, H/2, Math.max(W,H)*0.7);
    grad.addColorStop(0, '#0d0d1a');
    grad.addColorStop(1, '#050510');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);

    // Background stars
    bgStars.forEach(s => {{
      const a = 0.3 + 0.7 * Math.abs(Math.sin(time * s.speed + s.twinkle));
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI*2);
      ctx.fillStyle = `rgba(200,200,240,${{a.toFixed(2)}})`;
      ctx.fill();
    }});

    // Draw constellations
    constellations.forEach(con => {{
      const isHovered = hovered && hovered.constellation === con.name;
      const baseAlpha = isHovered ? 1 : (hovered ? 0.15 : 0.6);

      // Constellation lines
      ctx.strokeStyle = con.color;
      ctx.lineWidth = isHovered ? 1.5 : 0.8;
      ctx.globalAlpha = baseAlpha * 0.4;
      con.lines.forEach(([i,j]) => {{
        ctx.beginPath();
        ctx.moveTo(con.stars[i].sx, con.stars[i].sy);
        ctx.lineTo(con.stars[j].sx, con.stars[j].sy);
        ctx.stroke();
      }});

      // Stars
      con.stars.forEach(s => {{
        const twinkle = 0.7 + 0.3 * Math.sin(time * 1.5 + s.twinkle);
        ctx.globalAlpha = baseAlpha * s.brightness * twinkle;

        // Glow
        const grd = ctx.createRadialGradient(s.sx, s.sy, 0, s.sx, s.sy, s.size * 4);
        grd.addColorStop(0, con.color);
        grd.addColorStop(1, 'transparent');
        ctx.fillStyle = grd;
        ctx.beginPath();
        ctx.arc(s.sx, s.sy, s.size * 4, 0, Math.PI*2);
        ctx.fill();

        // Core
        ctx.fillStyle = '#fff';
        ctx.globalAlpha = baseAlpha * twinkle;
        ctx.beginPath();
        ctx.arc(s.sx, s.sy, s.size, 0, Math.PI*2);
        ctx.fill();
      }});

      // Label
      ctx.globalAlpha = isHovered ? 1 : (hovered ? 0.1 : 0.5);
      ctx.fillStyle = con.color;
      ctx.font = `${{isHovered ? 'bold 14' : '12'}}px SF Mono, Consolas, monospace`;
      ctx.textAlign = 'center';
      ctx.fillText(con.name.toUpperCase() + ` (${{con.count}})`, con.cx, con.cy - 80);
    }});

    ctx.globalAlpha = 1;
    time += 0.016;
    requestAnimationFrame(draw);
  }}

  canvas.addEventListener('mousemove', e => {{
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    hovered = null;
    for (const con of constellations) {{
      for (const s of con.stars) {{
        if (Math.hypot(s.sx - mx, s.sy - my) < 10) {{
          hovered = {{ ...s, constellation: con.name }};
          showTooltip(e, s);
          return;
        }}
      }}
    }}
    hideTooltip();
  }});

  // Info
  const info = document.createElement('div');
  info.className = 'info-panel';
  info.innerHTML = `<h3>Constellations</h3>
    <div class="stat">${{constellations.length}} constellations mapped</div>
    <div class="stat">Brightness = connectivity</div>
    <div style="margin-top:8px;color:#666">Hover to isolate a constellation</div>`;
  container.appendChild(info);

  draw();
}}

// ─── Init first tab ───
initGalaxy();
</script>
</body>
</html>"""


def main():
    print("Loading KB data...")
    nodes, edges, cluster_map, cluster_names = load_data()
    print(f"  {len(nodes)} nodes, {len(edges)} edges, {len(cluster_names)} clusters")

    print("Building HTML...")
    html = build_html(nodes, edges, cluster_map, cluster_names)

    PUBLISH.mkdir(exist_ok=True)
    out = PUBLISH / "cool_visualization.html"
    out.write_text(html)
    print(f"Written to {out} ({len(html)//1024}KB)")


if __name__ == "__main__":
    main()
