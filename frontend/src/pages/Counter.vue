<template>
  <div class="page">
    <h2 class="page-title">反证编排联查</h2>
    <p class="page-sub">
      对同一候选案件并行联查<b>药品进销存、配送签收、医师停诊、床位服务、医保政策、申诉结果</b>六类材料，
      明确“哪些合法解释已经成立、哪些仍未排除”。反证只做降权/关闭建议，不产生定性。
    </p>
    <el-row :gutter="14">
      <el-col :span="6">
        <div class="card">
          <h3>候选案件</h3>
          <div v-for="c in cases" :key="c.code"
               class="case-line" :class="{ active: code === c.code }" @click="select(c.code)">
            <div class="flex-between">
              <b class="small">{{ c.code }}</b>
              <el-tag size="small" :type="STATUS_TYPE[c.status] || 'info'">{{ statusShort(c.status) }}</el-tag>
            </div>
            <div class="small muted" style="line-height:1.5;margin:3px 0">{{ c.title }}</div>
            <el-progress :percentage="Math.round(c.counter_completeness * 100)" :stroke-width="7"
                         :status="c.counter_completeness >= 0.6 ? 'success' : ''" />
          </div>
        </div>
      </el-col>
      <el-col :span="18" v-if="detail">
        <div class="card">
          <div class="flex-between">
            <h3 style="margin:0">{{ detail.case.code }} · 六渠道联查结果</h3>
            <el-button size="small" @click="$router.push(`/cases/${detail.case.code}`)">进入案件投影 →</el-button>
          </div>
          <div class="flex gap8 mt12">
            <el-tag type="success">成立解释 {{ count.explains }}</el-tag>
            <el-tag type="success" effect="dark">确认反证 {{ count.confirmed }}</el-tag>
            <el-tag type="danger">未排除 {{ count.notExcluded }}</el-tag>
            <el-tag type="info">无材料 {{ count.noRecord }}</el-tag>
            <el-tag>完整度 {{ Math.round((judgment.counter_result?.completeness || 0) * 100) }}%</el-tag>
          </div>
        </div>
        <el-row :gutter="12">
          <el-col :span="12" v-for="c in detail.counter_checks" :key="c.check_type">
            <div class="card check-card" :class="c.status">
              <div class="flex-between">
                <b>{{ channelIcon(c.check_type) }} {{ c.title }}</b>
                <el-tag size="small" :type="type(c.status)">{{ label(c.status) }}</el-tag>
              </div>
              <p class="small" style="line-height:1.8;margin:10px 0">{{ c.detail }}</p>
              <div v-if="c.effect !== 'none'">
                <el-tag size="small" :type="c.effect === 'close' ? 'success' : 'warning'" effect="plain">
                  编排器建议：{{ c.effect === 'close' ? '关闭线索' : '排序降权' }}
                </el-tag>
              </div>
              <div v-if="c.source_codes?.length" class="mt8 small">
                <span class="muted">证据出处：</span>
                <router-link v-for="s in c.source_codes" :key="s" :to="`/sources?q=${encodeURIComponent(s)}`"
                             class="chain-uid" style="display:inline-block;margin:2px 4px 0 0">{{ s }}</router-link>
              </div>
            </div>
          </el-col>
        </el-row>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api.js'
import { STATUS_TYPE } from '../store.js'

const cases = ref([])
const code = ref('C-001')
const detail = ref(null)
const ICONS = { inventory: '📦', signature: '✍️', schedule: '📅', bed: '🛏️', policy: '📜', appeal: '📨' }
const channelIcon = (t) => ICONS[t] || '🔎'
function type(s) { return { checked_explains: 'success', confirmed: 'success', not_excluded: 'danger', no_record: 'info' }[s] }
function label(s) { return { checked_explains: '存在合理解释', confirmed: '反证确认', not_excluded: '仍未排除', no_record: '未查到材料' }[s] }
function statusShort(s) {
  return { pending: '待核', reviewing: '核查中', excluded_legitimate: '已排除', closed_returned: '已关闭', weak_identity: '弱身份' }[s] || s
}
const judgment = computed(() => detail.value?.current_judgment || {})
const count = computed(() => {
  const cs = detail.value?.counter_checks || []
  return {
    explains: cs.filter((c) => c.status === 'checked_explains').length,
    confirmed: cs.filter((c) => c.status === 'confirmed').length,
    notExcluded: cs.filter((c) => c.status === 'not_excluded').length,
    noRecord: cs.filter((c) => c.status === 'no_record').length
  }
})
async function select(c) {
  code.value = c
  detail.value = await api.get(`/api/cases/${c}`)
}
onMounted(async () => {
  cases.value = (await api.get('/api/cases')).items
  select('C-001')
})
</script>
<style scoped>
.case-line { padding: 9px 10px; border: 1px solid var(--rg-line); border-radius: 9px; margin-bottom: 8px; cursor: pointer; }
.case-line:hover { border-color: #93c5fd; }
.case-line.active { border-color: var(--rg-blue); background: #eff6ff; }
.check-card { border-left: 4px solid #cbd5e1; }
.check-card.checked_explains, .check-card.confirmed { border-left-color: #16a34a; }
.check-card.not_excluded { border-left-color: #dc2626; }
.check-card.no_record { border-left-color: #94a3b8; }
</style>
