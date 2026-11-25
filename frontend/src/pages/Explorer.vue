<template>
  <div class="page">
    <h2 class="page-title">受限图扩展</h2>
    <p class="page-sub">
      扩展范围由<b>起始索赔/受益人</b>与<b>允许边类型</b>共同定义。默认仅在同一伪标识/强关联簇内，
      沿处方/结算/供药边扩展两跳、180 天；弱身份候选只出现一条待核边（黄色虚线圈终端节点），不能继续扩展；
      灰色虚线为已否定边，默认不参与扩展。
    </p>

    <div class="card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="起点类型">
          <el-radio-group v-model="form.start_type">
            <el-radio-button value="beneficiary">受益人</el-radio-button>
            <el-radio-button value="claim">索赔/结算</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="编号">
          <el-input v-model="form.start_code" style="width:150px" placeholder="如 P1 / ST1" />
        </el-form-item>
        <el-form-item label="跳数">
          <el-input-number v-model="form.max_hops" :min="1" :max="4" />
        </el-form-item>
        <el-form-item label="窗口(天)">
          <el-input-number v-model="form.window_days" :min="1" :max="720" :step="30" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.include_weak">显示弱候选边</el-checkbox>
          <el-checkbox v-model="form.include_negated">包含已否定边</el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="run">扩展</el-button>
        </el-form-item>
      </el-form>
      <div class="flex gap8" style="flex-wrap:wrap">
        <span class="small muted">快捷起点：</span>
        <el-tag v-for="q in quick" :key="q.code" style="cursor:pointer" @click="pick(q)">{{ q.label }}</el-tag>
      </div>
      <el-collapse class="mt8">
        <el-collapse-item title="允许边类型（默认即题目规定的处方/结算/供药主链，可收紧）">
          <el-select v-model="form.edge_types" multiple collapse-tags collapse-tags-tooltip
                     style="width:100%" placeholder="留空使用默认集合">
            <el-option v-for="t in allEdgeTypes" :key="t" :label="`${edgeLabel(t)} (${t})`" :value="t" />
          </el-select>
        </el-collapse-item>
      </el-collapse>
    </div>

    <el-alert v-for="(n, i) in graph.notes || []" :key="i" type="warning" :title="n"
              class="mb8" :closable="false" show-icon />

    <el-row :gutter="14">
      <el-col :span="17">
        <div class="card">
          <div class="flex-between mb8">
            <h3 style="margin:0">关系子图（{{ graph.nodes?.length || 0 }} 节点 / {{ graph.edges?.length || 0 }} 边）</h3>
            <span class="small muted">可拖拽节点 · 点击边查看依据与出处</span>
          </div>
          <GraphCanvas v-if="graph.nodes?.length" :graph="graph"
                       @select-edge="selEdge = $event; selNode = null"
                       @select-node="selNode = $event; selEdge = null" />
          <el-empty v-else description="选择起点后点击“扩展”" />
        </div>
      </el-col>
      <el-col :span="7">
        <div class="card" style="min-height:300px">
          <h3>🔎 详情 / 依据出处</h3>
          <template v-if="selEdge">
            <el-tag size="small" :color="edgeMeta(selEdge.edge_type)?.color" style="color:#fff;border:none">
              {{ edgeLabel(selEdge.edge_type) }}
            </el-tag>
            <el-tag size="small" class="ml" :type="selEdge.stance === 'refute' ? 'success' : selEdge.stance === 'support' ? 'danger' : 'info'">
              {{ { support: '支持风险', refute: '反驳/解释', neutral: '中性' }[selEdge.stance] }}
            </el-tag>
            <el-tag v-if="selEdge.status === 'negated'" size="small" type="info">已否定</el-tag>
            <p style="line-height:1.8" class="mt8">{{ selEdge.basis }}</p>
            <div class="small muted">业务日期：{{ selEdge.event_date || '长期/无日期' }}</div>
            <div class="mt8"><b>可引用来源：</b></div>
            <div v-for="s in selEdge.source_codes" :key="s" class="chain-uid mt8" style="display:inline-block;margin-right:6px">
              <router-link :to="`/sources?q=${s}`">{{ s }}</router-link>
            </div>
            <div v-if="selEdge.props && Object.keys(selEdge.props).length" class="mt12">
              <b class="small">边属性</b>
              <pre class="detail-pre mt8">{{ JSON.stringify(selEdge.props, null, 2) }}</pre>
            </div>
          </template>
          <template v-else-if="selNode">
            <el-tag size="small" :color="nodeMeta(selNode.node_type)?.color" style="color:#fff;border:none">
              {{ nodeLabel(selNode.node_type) }}
            </el-tag>
            <h3 class="mt8">{{ selNode.label }}</h3>
            <div class="kv">
              <div class="k">UID</div><div class="mono small">{{ selNode.uid }}</div>
              <div class="k">编号</div><div>{{ selNode.ref_code }}</div>
              <div v-if="selNode.region" class="k">统筹区</div><div v-if="selNode.region">{{ selNode.region }}</div>
            </div>
            <pre class="detail-pre mt12">{{ JSON.stringify(selNode.props, null, 2) }}</pre>
          </template>
          <el-empty v-else description="点击节点或边查看具体依据" :image-size="80" />
        </div>

        <div class="card">
          <h3>🔐 高金额跨机构扩展</h3>
          <p class="small muted">金额 ≥ 模式门槛（默认 ¥50,000）时，才允许按模式配置显式增加边类型与截止条件，
            并可重新冻结快照。C-001 会被拒绝，C-007 可授权。</p>
          <div class="flex gap8">
            <el-input v-model="extCase" style="width:130px" />
            <el-button type="warning" :loading="extLoading" @click="runExt(false)">按配置扩展</el-button>
            <el-button type="danger" plain :loading="extLoading" @click="runExt(true)">扩展并冻结快照</el-button>
          </div>
          <el-alert v-if="extResult && !extResult.authorized" class="mt12" type="error"
                    :title="extResult.reason" :closable="false" />
          <div v-if="extResult?.authorized" class="mt12">
            <el-alert type="success" :closable="false"
                      :title="`已授权：新增边 ${extResult.extension.added_edge_types.join('、')}`" />
            <div class="small mt8">截止条件：{{ extResult.extension.extension_cutoff }}</div>
            <el-button size="small" class="mt8" @click="loadExtGraph">在左侧渲染扩展结果</el-button>
            <el-tag v-if="extResult.frozen_snapshot_no" type="danger" class="mt8" style="margin-left:8px">
              已冻结快照 #{{ extResult.frozen_snapshot_no }}
            </el-tag>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api.js'
import GraphCanvas from '../components/GraphCanvas.vue'
import { EDGE_META, NODE_META } from '../store.js'

const route = useRoute()
const form = reactive({ start_type: 'beneficiary', start_code: 'P1', max_hops: 2,
  window_days: 180, include_weak: true, include_negated: false, edge_types: [] })
const graph = ref({ nodes: [], edges: [], notes: [] })
const loading = ref(false)
const selEdge = ref(null), selNode = ref(null)
const allEdgeTypes = ref(Object.keys(EDGE_META))
const quick = [
  { label: 'P1 验收主链', start_type: 'beneficiary', code: 'P1' },
  { label: 'P2 弱身份', start_type: 'beneficiary', code: 'P2' },
  { label: 'ST5A 调拨组', start_type: 'claim', code: 'ST5A' },
  { label: 'P14 异常转诊', start_type: 'beneficiary', code: 'P14' }
]
const extCase = ref('C-007')
const extLoading = ref(false)
const extResult = ref(null)

const edgeLabel = (t) => EDGE_META[t]?.label || t
const edgeMeta = (t) => EDGE_META[t]
const nodeLabel = (t) => NODE_META[t]?.label || t
const nodeMeta = (t) => NODE_META[t]

function pick(q) { form.start_type = q.start_type; form.start_code = q.code; run() }

async function run() {
  loading.value = true
  try {
    const params = {
      start_type: form.start_type, start_code: form.start_code,
      max_hops: form.max_hops, window_days: form.window_days,
      include_weak: form.include_weak, include_negated: form.include_negated
    }
    if (form.edge_types.length) params.edge_types = form.edge_types.join(',')
    graph.value = await api.get('/api/graph/expand', { params })
  } finally { loading.value = false }
}
async function runExt(freeze) {
  extLoading.value = true
  try {
    extResult.value = await api.post('/api/graph/high-amount-extension',
      { case_code: extCase.value, freeze })
    if (extResult.value.authorized) graph.value = extResult.value
  } finally { extLoading.value = false }
}
function loadExtGraph() { if (extResult.value) graph.value = extResult.value }

onMounted(async () => {
  if (route.query.case === 'C-001') { form.start_code = 'P1' }
  run()
})
</script>
<style scoped>.ml { margin-left: 6px; }</style>
