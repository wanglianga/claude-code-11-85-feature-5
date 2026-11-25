<template>
  <div class="graph-box">
    <div class="graph-wrap">
      <svg ref="svgRef" class="graph-svg" viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid meet"
           @pointermove="onMove" @pointerup="onUp" @pointerleave="onUp">
        <defs>
          <marker id="arrow-neutral" markerWidth="8" markerHeight="8" refX="9" refY="3" orient="auto">
            <path d="M0,0 L9,3 L0,6 Z" fill="#94a3b8" />
          </marker>
        </defs>
        <g>
          <line v-for="e in renderEdges" :key="e.uid"
                :x1="pos(e.source).x" :y1="pos(e.source).y"
                :x2="pos(e.target).x" :y2="pos(e.target).y"
                :stroke="edgeColor(e)" :stroke-width="edgeWidth(e)"
                :stroke-dasharray="edgeDash(e)" :opacity="edgeOpacity(e)"
                style="cursor:pointer" @click.stop="$emit('select-edge', e)">
            <title>{{ EDGE_META[e.edge_type]?.label || e.edge_type }}｜{{ e.basis }}</title>
          </line>
        </g>
        <g v-for="n in renderNodes" :key="n.uid" class="node-pill"
           :opacity="nodeOpacity(n)"
           @click.stop="$emit('select-node', n)"
           @pointerdown="startDrag($event, n)">
          <circle :cx="n._x" :cy="n._y" :r="radius(n)" :fill="NODE_META[n.node_type]?.color || '#475569'"
                  :stroke="terminalSet.has(n.uid) ? '#d97706' : '#fff'"
                  :stroke-width="terminalSet.has(n.uid) ? 3 : 1.5"
                  :stroke-dasharray="terminalSet.has(n.uid) ? '4 3' : ''" />
          <text :x="n._x" :y="n._y + radius(n) + 13" text-anchor="middle"
                font-size="11.5" fill="#1e293b" font-weight="600" style="pointer-events:none">
            {{ shortLabel(n.label) }}
          </text>
          <text v-if="false" :x="n._x" :y="n._y + 3" text-anchor="middle" font-size="10" fill="#fff"></text>
          <title>{{ n.label }}</title>
        </g>
      </svg>
    </div>
    <div v-if="showLegend" class="legend">
      <span class="item" v-for="t in usedNodeTypes" :key="'n'+t">
        <span class="swatch" :style="{ background: NODE_META[t]?.color, height: '10px', width: '10px', borderRadius: '50%' }"></span>
        {{ NODE_META[t]?.label || t }}
      </span>
      <span class="item" v-for="t in usedEdgeTypes" :key="'e'+t">
        <span class="swatch" :style="{ background: EDGE_META[t]?.color || '#94a3b8' }"></span>
        {{ EDGE_META[t]?.label || t }}
      </span>
      <span class="item"><span class="swatch" style="background:#94a3b8; opacity:.5"></span>已否定边（不扩展）</span>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { EDGE_META, NODE_META } from '../store.js'

const props = defineProps({
  graph: { type: Object, default: () => ({ nodes: [], edges: [] }) },
  highlightUids: { type: Array, default: () => [] },
  showLegend: { type: Boolean, default: true }
})
defineEmits(['select-node', 'select-edge'])

const W = 1000, H = 600
const svgRef = ref(null)
const positioned = ref([])
const dragNode = ref(null)

const hiSet = computed(() => new Set(props.highlightUids || []))
const terminalSet = computed(() => new Set(props.graph?.terminal_candidates || []))

const nodeMap = computed(() => Object.fromEntries(positioned.value.map((n) => [n.uid, n])))

function pos(uid) {
  const n = nodeMap.value[uid]
  return n ? { x: n._x, y: n._y } : { x: -100, y: -100 }
}
function radius(n) {
  return n.node_type === 'person' ? 13 : 10
}
function shortLabel(s) {
  if (!s) return ''
  return s.length > 13 ? s.slice(0, 12) + '…' : s
}
function edgeColor(e) {
  if (e.status === 'negated') return '#94a3b8'
  if (e.stance === 'refute') return '#16a34a'
  return EDGE_META[e.edge_type]?.color || '#94a3b8'
}
function edgeWidth(e) { return EDGE_META[e.edge_type]?.width || 1.6 }
function edgeDash(e) {
  if (e.status === 'negated') return '3 4'
  return EDGE_META[e.edge_type]?.dash || ''
}
function edgeOpacity(e) {
  if (hiSet.value.size) return hiSet.value.has(e.uid) ? 1 : 0.1
  return e.status === 'negated' ? 0.55 : 0.9
}
function nodeOpacity(n) {
  if (!hiSet.value.size) return 1
  const endpoints = new Set()
  for (const e of props.graph.edges || []) {
    if (hiSet.value.has(e.uid)) { endpoints.add(e.source); endpoints.add(e.target) }
  }
  return endpoints.has(n.uid) ? 1 : 0.3
}

const renderNodes = computed(() => positioned.value)
const renderEdges = computed(() => props.graph?.edges || [])
const usedNodeTypes = computed(() => [...new Set((props.graph?.nodes || []).map((n) => n.node_type))])
const usedEdgeTypes = computed(() => [...new Set((props.graph?.edges || []).map((e) => e.edge_type))])

function layout() {
  const nodes = (props.graph?.nodes || []).map((n, i) => {
    const angle = (i / Math.max(1, props.graph.nodes.length)) * Math.PI * 2
    return { ...n,
      _x: W / 2 + Math.cos(angle) * (260 + (i % 3) * 40),
      _y: H / 2 + Math.sin(angle) * (190 + (i % 4) * 26),
      _vx: 0, _vy: 0 }
  })
  const idx = Object.fromEntries(nodes.map((n, i) => [n.uid, i]))
  const edges = (props.graph?.edges || [])
    .filter((e) => idx[e.source] !== undefined && idx[e.target] !== undefined)
    .map((e) => ({ a: idx[e.source], b: idx[e.target] }))
  // 轻量力导向
  for (let iter = 0; iter < 260; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = nodes[j]._x - nodes[i]._x, dy = nodes[j]._y - nodes[i]._y
        let d2 = dx * dx + dy * dy || 1
        const f = 2600 / d2
        const d = Math.sqrt(d2)
        dx /= d; dy /= d
        nodes[i]._vx -= dx * f; nodes[i]._vy -= dy * f
        nodes[j]._vx += dx * f; nodes[j]._vy += dy * f
      }
    }
    for (const e of edges) {
      const a = nodes[e.a], b = nodes[e.b]
      const dx = b._x - a._x, dy = b._y - a._y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const f = (d - 130) * 0.012
      const fx = (dx / d) * f, fy = (dy / d) * f
      a._vx += fx; a._vy += fy
      b._vx -= fx; b._vy -= fy
    }
    for (const n of nodes) {
      n._vx += (W / 2 - n._x) * 0.002
      n._vy += (H / 2 - n._y) * 0.002
      n._x += n._vx * 0.9; n._y += n._vy * 0.9
      n._vx *= 0.82; n._vy *= 0.82
      n._x = Math.max(40, Math.min(W - 40, n._x))
      n._y = Math.max(34, Math.min(H - 30, n._y))
    }
  }
  positioned.value = nodes
}

function svgPoint(ev) {
  const rect = svgRef.value.getBoundingClientRect()
  return { x: ((ev.clientX - rect.left) / rect.width) * W,
           y: ((ev.clientY - rect.top) / rect.height) * H }
}
function startDrag(ev, n) {
  dragNode.value = n
  ev.preventDefault()
}
function onMove(ev) {
  if (!dragNode.value) return
  const p = svgPoint(ev)
  const live = positioned.value.find((n) => n.uid === dragNode.value.uid)
  if (live) { live._x = p.x; live._y = p.y }
}
function onUp() { dragNode.value = null }

watch(() => props.graph, () => nextTick(layout), { deep: false, immediate: true })
</script>
