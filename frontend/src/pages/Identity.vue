<template>
  <div class="page">
    <h2 class="page-title">实体与身份证据解析</h2>
    <p class="page-sub">
      医保伪标识、有效期内专业凭证 → <b>强证据自动成簇</b>；单独共享电话、地址、代办人 →
      <b>弱候选待核，不自动合并个人</b>。人工裁定会产生新的身份簇版本与图快照，并标出受影响案件。
    </p>

    <el-row :gutter="14">
      <el-col :span="15">
        <div class="card">
          <h3>🪪 证据分级与裁定</h3>
          <el-table :data="evidences" size="small" border max-height="640">
            <el-table-column label="主体" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="row.subject_type === 'person' ? '' : 'warning'">
                  {{ row.subject_type === 'person' ? '人员' : '执业实体' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="证据双方" min-width="170">
              <template #default="{ row }">
                <b>{{ row.left_code }}</b> {{ row.left_name !== row.left_code ? row.left_name : '' }}
                ↔ <b>{{ row.right_code }}</b>
                <div class="small muted">{{ row.right_name !== row.right_code ? row.right_name : '' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="证据类型 / 内容" min-width="220">
              <template #default="{ row }">
                <el-tag size="small" :type="row.tier === 'strong' ? 'success' : 'warning'" effect="dark">
                  {{ row.tier === 'strong' ? '强' : '弱' }}
                </el-tag>
                <span class="small" style="margin-left:6px">{{ typeLabel(row.evidence_type) }}</span>
                <div class="small muted">{{ row.evidence_value }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="105">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="人工裁定" width="150">
              <template #default="{ row }">
                <template v-if="row.status === 'pending'">
                  <el-button size="small" type="success" link @click="adjudicate(row, 'confirmed')">确认合并</el-button>
                  <el-button size="small" type="danger" link @click="adjudicate(row, 'rejected')">驳回</el-button>
                </template>
                <span v-else class="small muted">{{ row.decided_by }}<br />{{ row.decision_note || '' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
      <el-col :span="9">
        <div class="card">
          <h3>🧬 身份簇版本（不可变）</h3>
          <el-timeline>
            <el-timeline-item v-for="v in versions" :key="v.version"
                              :timestamp="`v${v.version} · ${fmt(v.created_at)}`" placement="top">
              <div class="small" style="margin-bottom:6px">{{ v.reason }}</div>
              <div v-for="c in v.clusters.filter(x => x.members.length > 1)" :key="c.cluster" class="chain-step">
                <b>{{ c.cluster }}</b>：{{ c.members.join('、') }}
              </div>
              <div class="small muted">其余自然人均为单成员簇（弱候选不并入）。</div>
            </el-timeline-item>
          </el-timeline>
        </div>
      </el-col>
    </el-row>

    <el-dialog v-model="dlg" title="裁定弱身份候选" width="440px">
      <p class="small muted">{{ current?.left_code }} ↔ {{ current?.right_code }}：{{ current?.evidence_value }}</p>
      <el-input v-model="note" type="textarea" :rows="3" placeholder="核查说明（将写入审计与新快照原因）" />
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="danger" @click="submit('rejected')">驳回（不是同一人）</el-button>
        <el-button type="success" @click="submit('confirmed')">确认强关联并合并</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api.js'
import { ElMessage } from 'element-plus'

const evidences = ref([])
const versions = ref([])
const dlg = ref(false)
const note = ref('')
const current = ref(null)
const pendingDecision = ref('')
const router = useRouter()

const TYPE = {
  pseudo_id: '医保伪标识', professional_credential: '有效期内专业凭证',
  shared_phone: '共享电话', shared_address: '共享地址', shared_agent: '共同代办人'
}
const typeLabel = (t) => TYPE[t] || t
const statusLabel = (s) => ({ auto: '强证据自动成簇', pending: '弱候选待核', confirmed: '人工确认', rejected: '已驳回' }[s] || s)
const statusType = (s) => ({ auto: 'success', pending: 'warning', confirmed: 'success', rejected: 'info' }[s] || '')
function fmt(s) { return s ? s.replace('T', ' ').slice(5, 16) : '' }

async function load() {
  evidences.value = (await api.get('/api/identity/evidence')).items
  versions.value = (await api.get('/api/identity/clusters')).items
}
function adjudicate(row, decision) {
  current.value = row
  pendingDecision.value = decision
  note.value = decision === 'rejected' ? '电话为养老机构公用，不能据此认定同一人' : ''
  dlg.value = true
}
async function submit(decision) {
  const r = await api.post(`/api/identity/evidence/${current.value.code}/adjudicate`,
    { decision, note: note.value })
  dlg.value = false
  ElMessage.success(`已生成快照 #${r.snapshot_no}，受影响案件：${r.affected_cases.join('、') || '无'}`)
  await load()
}
onMounted(load)
</script>
