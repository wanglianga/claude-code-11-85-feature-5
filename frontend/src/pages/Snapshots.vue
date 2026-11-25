<template>
  <div class="page">
    <h2 class="page-title">图快照与复演</h2>
    <p class="page-sub">
      每个快照冻结<b>分析截止日、身份簇版本、边范围与政策版本</b>。关系裁定、政策有效日变化、退费更正都会生成新快照并标出受影响案件；
      旧案件判断仍指向旧快照，原判断来源不被覆盖。
    </p>

    <el-row :gutter="14">
      <el-col :span="7">
        <div class="card">
          <h3>📸 快照列表</h3>
          <div v-for="s in snapshots" :key="s.snapshot_no" class="snap-line"
               :class="{ active: no === s.snapshot_no }" @click="open(s.snapshot_no)">
            <div class="flex-between">
              <b>#{{ s.snapshot_no }} {{ s.label }}</b>
              <span class="small muted">{{ s.cutoff_date }}</span>
            </div>
            <div class="small muted" style="line-height:1.6;margin-top:3px">{{ s.trigger_reason }}</div>
            <div class="small mt8">
              <el-tag size="small">身份 v{{ s.identity_version }}</el-tag>
              <el-tag size="small" type="info" class="ml">{{ s.counts.nodes }}节点/{{ s.counts.edges }}边</el-tag>
              <el-tag v-if="s.affected_case_codes?.length" size="small" type="danger" class="ml">
                影响 {{ s.affected_case_codes.length }} 案
              </el-tag>
            </div>
          </div>
        </div>

        <div class="card" v-if="store.isAdmin">
          <h3>🧨 触发变更并冻结新快照</h3>
          <el-form label-width="92px" size="small">
            <el-divider content-position="left">政策有效日更正/换版</el-divider>
            <el-form-item label="政策">
              <el-select v-model="policy.policy_code" style="width:100%">
                <el-option label="慢病长处方办法 POL-CHRONIC" value="POL-CHRONIC" />
                <el-option label="用药限量 POL-RX-LIMIT" value="POL-RX-LIMIT" />
                <el-option label="转诊规程 POL-REFERRAL" value="POL-REFERRAL" />
              </el-select>
            </el-form-item>
            <el-form-item label="新版本"><el-input v-model="policy.new_version" /></el-form-item>
            <el-form-item label="生效日"><el-date-picker v-model="policy.effective_from" value-format="YYYY-MM-DD" style="width:100%" /></el-form-item>
            <el-button type="warning" size="small" :loading="acting" @click="policyChange">生成政策快照</el-button>

            <el-divider content-position="left">退费/更正</el-divider>
            <el-form-item label="结算编号"><el-input v-model="refund.settlement_code" /></el-form-item>
            <el-form-item label="退费金额"><el-input-number v-model="refund.refund_amount" :min="1" /></el-form-item>
            <el-button type="danger" plain size="small" :loading="acting" @click="refundChange">生成退费更正快照</el-button>
            <p class="small muted mt8">演示默认对拆方案件 C-005 的 ST12 退费 ¥168；可在案件判断版本中对比复判结果。</p>
          </el-form>
        </div>
      </el-col>

      <el-col :span="17" v-if="snap">
        <div class="card">
          <div class="flex-between">
            <h3 style="margin:0">快照 #{{ snap.snapshot_no }} 复演：{{ snap.label }}</h3>
            <el-tag>{{ snap.cutoff_date }} 截止</el-tag>
          </div>
          <p class="small" style="line-height:1.8">{{ snap.trigger_reason }}</p>
          <div class="flex gap8" style="flex-wrap:wrap">
            <el-tag type="primary">身份簇版本 v{{ snap.identity_version }}</el-tag>
            <el-tag v-for="p in snap.policy_versions" :key="p.code" type="info">
              {{ p.code }}@{{ p.version }}（{{ p.effective_from }}）
            </el-tag>
          </div>
          <el-descriptions :column="2" border size="small" class="mt12">
            <el-descriptions-item label="边范围">
              <span class="mono small wrap-json">{{ JSON.stringify(snap.edge_scope, null, 0) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="冻结统计">
              {{ snap.counts.nodes }} 节点 / {{ snap.counts.edges }} 边；
              支持 {{ snap.counts.support_edges }}、反驳 {{ snap.counts.refute_edges }}、
              否定 {{ snap.counts.negated_edges }}
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="snap.affected_case_codes?.length" class="mt12">
            <b class="small">受影响案件：</b>
            <el-button v-for="c in snap.affected_case_codes" :key="c" link type="danger" size="small"
                       @click="$router.push(`/cases/${c}`)">{{ c }} →</el-button>
          </div>
        </div>

        <div class="card">
          <h3>冻结图谱（只读复演）</h3>
          <GraphCanvas :graph="snap.graph" :show-legend="true" />
        </div>

        <div class="card">
          <h3>快照 Diff</h3>
          <div class="flex gap8">
            <el-input-number v-model="diffFrom" :min="1" :max="snapshots.length" size="small" />
            <span>→</span>
            <el-input-number v-model="diffTo" :min="1" :max="snapshots.length" size="small" />
            <el-button size="small" type="primary" @click="loadDiff">比较</el-button>
          </div>
          <div v-if="diff" class="mt12 small">
            <el-tag type="success">新增 {{ diff.added.length }}</el-tag>
            <el-tag type="danger" class="ml">移除 {{ diff.removed.length }}</el-tag>
            <el-tag type="warning" class="ml">状态变化 {{ diff.status_changed.length }}</el-tag>
            <div v-for="x in diff.status_changed.slice(0, 12)" :key="x.uid" class="chain-step mt8">
              <span class="mono">{{ x.uid }}</span>：{{ x.from_status }} → {{ x.to_status }}
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../api.js'
import GraphCanvas from '../components/GraphCanvas.vue'
import { store } from '../store.js'
import { ElMessage } from 'element-plus'

const snapshots = ref([])
const no = ref(1)
const snap = ref(null)
const acting = ref(false)
const diff = ref(null)
const diffFrom = ref(1), diffTo = ref(2)
const policy = ref({ policy_code: 'POL-CHRONIC', new_version: 'v2026.3', effective_from: '2026-03-15' })
const refund = ref({ settlement_code: 'ST12', refund_amount: 168 })

async function loadList(selectNo) {
  snapshots.value = (await api.get('/api/snapshots')).items
  if (selectNo) await open(selectNo)
  else if (snapshots.value.length) await open(snapshots.value[0].snapshot_no)
  diffTo.value = snapshots.value[0]?.snapshot_no || 1
}
async function open(n) {
  no.value = n
  snap.value = await api.get(`/api/snapshots/${n}`)
}
async function policyChange() {
  acting.value = true
  try {
    const r = await api.post('/api/snapshots/policy-change', policy.value)
    ElMessage.success(`快照 #${r.snapshot_no} 已生成，影响 ${r.affected_cases.join('、') || '无'}`)
    await loadList(r.snapshot_no)
  } finally { acting.value = false }
}
async function refundChange() {
  acting.value = true
  try {
    const r = await api.post('/api/snapshots/refund-correction', refund.value)
    ElMessage.success(`快照 #${r.snapshot_no} 已生成，结算状态 ${r.settlement_status}`)
    await loadList(r.snapshot_no)
  } finally { acting.value = false }
}
async function loadDiff() {
  diff.value = await api.get('/api/snapshots/diff', { params: { from_no: diffFrom.value, to_no: diffTo.value } })
}
onMounted(() => loadList(1))
</script>
<style scoped>
.snap-line { border: 1px solid var(--rg-line); border-radius: 9px; padding: 10px 12px; margin-bottom: 9px; cursor: pointer; }
.snap-line:hover { border-color: #93c5fd; }
.snap-line.active { border-color: var(--rg-blue); background: #eff6ff; }
.ml { margin-left: 6px; }
.wrap-json { white-space: pre-wrap; word-break: break-all; line-height: 1.6; }
:deep(.el-descriptions__content) { max-width: 680px; }
</style>
