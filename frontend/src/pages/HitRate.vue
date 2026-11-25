<template>
  <div class="page">
    <h2 class="page-title">冻结排序 Top-10% 命中率评估</h2>
    <p class="page-sub">
      目标量是<b>冻结候选批次中经规定证据确认欺诈的比例</b>，不是已办案件阳性率。
      候选集、名次与分数在截止日冻结；<b>确认标签、右删失/待核状态、概率预测分别保存，待核件不计阴性</b>。
      页面同时给出分层随机复核路与已办概率校正路的点估计、95% 区间、重叠/分歧与不可识别护栏。
    </p>

    <div class="card">
      <div class="flex-between">
        <div class="flex gap8">
          <span class="muted small">冻结批次：</span>
          <el-select v-model="batchCode" style="width:340px" @change="loadAll">
            <el-option v-for="b in batches" :key="b.code"
                       :label="`${b.code}｜${b.title}`" :value="b.code" />
          </el-select>
          <el-tag v-if="batch" size="small" type="info">
            池 {{ batch.scope.universe_size }} → Top {{ batch.scope.top_n }}
            （{{ batch.cutoff_date }} 截止，{{ batch.ranking_version }}）
          </el-tag>
        </div>
        <el-button v-if="store.isAdmin" type="primary" plain :loading="evaluating"
                   @click="reevaluate">重新运行双路评估</el-button>
      </div>
      <el-alert class="mt12" type="warning" :closable="false"
                title="评分与命中率都只是核查优先级证据，不是欺诈或违法结论；定性须经人工核查与法定程序。" />
    </div>

    <template v-if="run">
      <!-- 两路点估计 + 区间 -->
      <el-row :gutter="14">
        <el-col :span="8">
          <div class="card estimate-card">
            <div class="flex-between">
              <h3 style="margin:0">① 分层随机复核路</h3>
              <el-tag size="small" :type="rand.identifiable ? 'success' : 'danger'">
                {{ rand.identifiable ? '可识别' : '不可识别' }}
              </el-tag>
            </div>
            <p class="small muted" style="line-height:1.7">
              仅用分层随机复核推断未核查总体；设计推断，不依赖选择模型。
            </p>
            <template v-if="rand.identifiable">
              <div class="big-number">{{ rand.point_estimate }}%</div>
              <div class="small">点估计：约 {{ rand.estimated_confirmed }} / {{ rand.N }} 件确认</div>
              <div class="mt8">
                <el-tag type="primary" size="small">95% 区间 {{ rand.ci95[0] }}% ~ {{ rand.ci95[1] }}%</el-tag>
              </div>
              <div class="small muted mt8">{{ rand.ci95_method }}</div>
              <div class="formula mono">T_h = y_hist_h + k_h ÷ (m_h/M_h)</div>
            </template>
            <el-alert v-else type="error" :closable="false" class="mt8"
                      :title="rand.required_action || '存在不可识别层，不能外推'"
                      description="阻断层见下，该路不展示命中率" show-icon />
            <div v-for="x in rand.blocked_strata" :key="x.stratum" class="block-line">
              <el-tag size="small" type="danger">{{ x.stratum }} 层</el-tag>
              <span class="small">{{ x.reason }}</span>
            </div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="card estimate-card">
            <div class="flex-between">
              <h3 style="margin:0">② 已办概率校正路（IPW）</h3>
              <el-tag size="small" :type="ipw.identifiable ? 'success' : 'danger'">
                {{ ipw.identifiable ? '可识别' : '不可识别' }}
              </el-tag>
            </div>
            <p class="small muted" style="line-height:1.7">
              历史已办按记录核查概率 1/π 加权；更快，但依赖条件独立（CIA）假设。
            </p>
            <template v-if="ipw.identifiable">
              <div class="big-number">{{ ipw.point_estimate }}%</div>
              <div class="small">点估计：约 {{ ipw.estimated_confirmed }} / {{ ipw.N }} 件确认</div>
              <div class="mt8">
                <el-tag type="primary" size="small">95% 区间 {{ ipw.ci95[0] }}% ~ {{ ipw.ci95[1] }}%</el-tag>
              </div>
              <div class="small muted mt8">{{ ipw.ci95_method }}</div>
              <div class="formula mono">T_h = y_hist_h ÷ π_h</div>
            </template>
            <el-alert v-else type="error" :closable="false" class="mt8"
                      :title="ipw.required_action || '存在不可识别层，不能外推'" show-icon />
            <div v-for="x in ipw.blocked_strata" :key="x.stratum" class="block-line">
              <el-tag size="small" type="danger">{{ x.stratum }} 层</el-tag>
              <span class="small">{{ x.reason }}</span>
            </div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="card estimate-card">
            <h3>对比与结论</h3>
            <template v-if="comparison.overlap !== null && comparison.overlap !== undefined">
              <div class="flex gap8 mb8">
                <el-tag size="small" :type="comparison.overlap ? 'success' : 'danger'">
                  区间{{ comparison.overlap ? '重叠' : '不重叠' }}
                </el-tag>
                <el-tag size="small" :type="Math.abs(comparison.divergence_pp) <= 5 ? 'success' : 'warning'">
                  点估计差 {{ comparison.divergence_pp }} pp
                </el-tag>
              </div>
              <p class="small" style="line-height:1.8">{{ comparison.verdict }}</p>
            </template>
            <el-alert v-else type="error" :closable="false" :title="comparison.verdict" show-icon />
            <el-divider style="margin:10px 0" />
            <div class="small muted">已办案件阳性率（对照，非目标量）</div>
            <div class="flex-between mt8">
              <b class="naive-rate">{{ accounting.naive_adjudicated?.rate_pp }}%</b>
              <el-tag size="small" type="info">已办 {{ accounting.naive_adjudicated?.reviewed }} 件 /
                确认 {{ accounting.naive_adjudicated?.confirmed }} 件</el-tag>
            </div>
            <p class="small muted mt8" style="line-height:1.7">{{ accounting.naive_adjudicated?.warning }}</p>
            <el-divider style="margin:10px 0" />
            <el-tag :type="ident.can_claim_ranking_lift ? 'success' : 'danger'" effect="dark">
              {{ ident.can_claim_ranking_lift ? '可在持续复核下报告排序命中' : '不得宣称排序提升命中' }}
            </el-tag>
          </div>
        </el-col>
      </el-row>

      <!-- 不可识别护栏 -->
      <div class="card" v-if="!ident.can_claim_ranking_lift || ident.unrecorded_confounders?.length">
        <h3>🚧 可识别性护栏</h3>
        <el-alert type="error" :closable="false" class="mb8"
                  title="关键层核查概率为零，或转介等因素同时影响核查与欺诈却未记录：选择模型不可识别，不能外推该层；须扩大随机复核，且不得宣称排序提升命中。" />
        <el-table :data="guardRows" border size="small">
          <el-table-column prop="path" label="路径" width="150" />
          <el-table-column prop="stratum" label="层" width="90" />
          <el-table-column prop="reason" label="原因" min-width="360" show-overflow-tooltip />
        </el-table>
      </div>

      <!-- 分层核查概率账 -->
      <div class="card">
        <h3>📒 分层核查概率账（A/B 各 50 件）</h3>
        <el-table :data="stratumRows" border size="small">
          <el-table-column prop="region" label="地区" width="70" align="center">
            <template #default="{ row }"><b>{{ row.region }}</b></template>
          </el-table-column>
          <el-table-column label="层规模" width="80" align="center">
            <template #default="{ row }">{{ row.N }}</template>
          </el-table-column>
          <el-table-column label="历史核查（确认/数）" width="150" align="center">
            <template #default="{ row }">
              {{ row.y_hist }} / {{ row.n_hist }}
              <el-tag size="small" type="warning" class="ml">π={{ row.pi_hist ?? '—' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="未核查 M" width="90" align="center">
            <template #default="{ row }">{{ row.M_unreviewed }}</template>
          </el-table-column>
          <el-table-column label="随机复核（确认 m 中 k）" width="170" align="center">
            <template #default="{ row }">
              {{ row.k_audit }} / {{ row.m_audit }}
              <el-tag size="small" type="primary" class="ml">π={{ row.pi_audit ?? '—' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="待核右删失（不计阴性）" width="160" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.n_censored }} 件</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="随机路层估计（95% 精确区间）" min-width="220">
            <template #default="{ row }">
              <template v-if="rand.per_stratum?.[row.region]?.ht_estimate_total != null">
                <b>{{ rand.per_stratum[row.region].ht_estimate_total }}</b> 件
                <span class="small muted">（{{ rand.per_stratum[row.region].ci95_exact[0] }}%
                  ~ {{ rand.per_stratum[row.region].ci95_exact[1] }}%）</span>
              </template>
              <span v-else class="small fact-fail">无概率样本，不可估计</span>
            </template>
          </el-table-column>
          <el-table-column label="IPW 层估计" width="130" align="center">
            <template #default="{ row }">
              <template v-if="ipw.per_stratum?.[row.region]?.ipw_estimate_total != null">
                {{ ipw.per_stratum[row.region].ipw_estimate_total }} 件
                <div class="small muted">权重 1/π={{ ipw.per_stratum[row.region].weight }}</div>
              </template>
              <span v-else class="small fact-fail">不可外推</span>
            </template>
          </el-table-column>
        </el-table>
        <p class="small muted mt12" style="line-height:1.8">
          随机路：A 层 {{ formulaA }}；B 层 {{ formulaB }}。
          概率校正路依赖“核查选择在地区、分数及已记录转介因素条件下可忽略”，偏差可控但需要持续随机复核校准。
        </p>
      </div>

      <!-- 冻结候选集 -->
      <div class="card">
        <div class="flex-between mb8">
          <h3 style="margin:0">🧊 冻结候选集与证据标签（{{ candidates.length }} 件，可筛选）</h3>
          <div class="flex gap8">
            <el-select v-model="fRegion" style="width:110px" @change="loadCandidates" clearable
                       placeholder="地区">
              <el-option label="A 地区" value="A" /><el-option label="B 地区" value="B" />
            </el-select>
            <el-select v-model="fCensor" style="width:140px" @change="loadCandidates" clearable
                       placeholder="删失状态">
              <el-option label="右删失/待核" value="censored" /><el-option label="已出结论" value="resolved" />
            </el-select>
            <el-select v-model="fSource" style="width:150px" @change="loadCandidates" clearable
                       placeholder="核查来源">
              <el-option label="历史核查" value="historical" />
              <el-option label="随机复核" value="random_audit" />
              <el-option label="未核查" value="none" />
            </el-select>
          </div>
        </div>
        <el-table :data="candidates" border size="small" max-height="460">
          <el-table-column prop="rank_no" label="冻结名次" width="84" align="center" />
          <el-table-column prop="candidate_code" label="候选编号" width="100" />
          <el-table-column prop="region" label="地区" width="64" align="center" />
          <el-table-column label="冻结分/段" width="110">
            <template #default="{ row }">
              <b>{{ row.frozen_score }}</b>
              <el-tag size="small" class="ml" :type="bandType(row.score_band)">{{ bandLabel(row.score_band) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="核查来源 / 概率 π" width="170">
            <template #default="{ row }">
              <el-tag size="small" :type="sourceType(row.review_source)">{{ sourceLabel(row.review_source) }}</el-tag>
              <div class="small muted">{{ row.review_probability != null ? 'π=' + row.review_probability : '—' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="证据确认标签" width="130">
            <template #default="{ row }">
              <el-tag v-if="row.evidence_label === 'confirmed'" size="small" type="danger">规定证据确认</el-tag>
              <el-tag v-else-if="row.evidence_label === 'cleared'" size="small" type="success">经证据排除</el-tag>
              <el-tag v-else size="small" type="info">待核·无标签</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="删失" width="92" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.censor_status === 'censored' ? 'info' : 'success'">
                {{ row.censor_status === 'censored' ? '右删失' : '已结论' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="证据出处 / 概率依据" min-width="260">
            <template #default="{ row }">
              <div class="small mono">{{ (row.evidence_codes || []).join('、') || '—' }}</div>
              <div class="small muted">{{ row.probability_basis || '—' }}</div>
            </template>
          </el-table-column>
          <el-table-column v-if="store.isAdmin" label="操作" width="110" align="center">
            <template #default="{ row }">
              <el-button v-if="row.review_source === 'none'" link type="primary" size="small"
                         @click="openAudit(row)">登记复核</el-button>
              <span v-else class="small muted">已留痕</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 评估留痕 -->
      <div class="card">
        <h3>🗂 评估运行留痕</h3>
        <el-table :data="runs" border size="small">
          <el-table-column prop="id" label="#" width="60" align="center" />
          <el-table-column prop="run_at" label="运行时间" width="200">
            <template #default="{ row }">{{ row.run_at?.replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column prop="author" label="操作人" width="100" />
          <el-table-column label="随机路（95% CI）" width="200">
            <template #default="{ row }">
              {{ row.random_point != null ? `${row.random_point}% [${row.random_ci95[0]}~${row.random_ci95[1]}]` : '不可识别' }}
            </template>
          </el-table-column>
          <el-table-column label="IPW（95% CI）" width="200">
            <template #default="{ row }">
              {{ row.ipw_point != null ? `${row.ipw_point}% [${row.ipw_ci95[0]}~${row.ipw_ci95[1]}]` : '不可识别' }}
            </template>
          </el-table-column>
          <el-table-column prop="method_note" label="说明" min-width="220" show-overflow-tooltip />
        </el-table>
      </div>
    </template>

    <!-- 登记随机复核 -->
    <el-dialog v-model="auditDlg" title="登记随机复核结论（待核件 → 已结论）" width="520px">
      <el-alert type="info" :closable="false" class="mb8"
                title="该候选当前为右删失/待核，无证据标签且不计阴性；登记后按规定证据写入确认/排除，并更新该层随机复核入样概率。" />
      <el-form label-width="110px" size="small">
        <el-form-item label="候选编号">
          <b>{{ auditRow?.candidate_code }}</b>（{{ auditRow?.region }} 层，名次 {{ auditRow?.rank_no }}）
        </el-form-item>
        <el-form-item label="证据标签">
          <el-radio-group v-model="auditForm.evidence_label">
            <el-radio value="confirmed">规定证据确认欺诈</el-radio>
            <el-radio value="cleared">经证据排除</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="证据编号">
          <el-input v-model="auditForm.evidence_code" placeholder="如 EV-RA-C06（规定证据单据号）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="auditDlg = false">取消</el-button>
        <el-button type="primary" :loading="savingAudit" @click="submitAudit">保存并留痕</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api.js'
import { store } from '../store.js'
import { ElMessage } from 'element-plus'

const batches = ref([])
const batchCode = ref('')
const batch = ref(null)
const run = ref(null)
const accounting = ref({ strata: [], naive_adjudicated: {} })
const candidates = ref([])
const runs = ref([])
const evaluating = ref(false)
const fRegion = ref(''), fCensor = ref(''), fSource = ref('')
const auditDlg = ref(false), savingAudit = ref(false)
const auditRow = ref(null)
const auditForm = ref({ evidence_label: 'confirmed', evidence_code: '' })

const rand = computed(() => run.value?.random_audit || {})
const ipw = computed(() => run.value?.ipw || {})
const comparison = computed(() => run.value?.comparison || {})
const ident = computed(() => run.value?.identifiability || {})
const stratumRows = computed(() => accounting.value.strata || [])
const formulaA = computed(() => {
  const a = stratumRows.value.find((s) => s.region === 'A')
  return a ? `20 + 2÷(${a.m_audit}/${a.M_unreviewed}) = ${rand.value.per_stratum?.A?.ht_estimate_total ?? '—'} 件` : ''
})
const formulaB = computed(() => {
  const b = stratumRows.value.find((s) => s.region === 'B')
  return b ? `3 + 2÷(${b.m_audit}/${b.M_unreviewed}) = ${rand.value.per_stratum?.B?.ht_estimate_total ?? '—'} 件` : ''
})
const guardRows = computed(() => {
  const rows = []
  for (const x of ident.value.random_audit_issues || []) rows.push({ path: '随机复核路', ...x })
  for (const x of ident.value.ipw_issues || []) rows.push({ path: '概率校正路', ...x })
  for (const x of ident.value.unrecorded_confounders || []) {
    rows.push({ path: '概率校正路', stratum: x.region, reason: `${x.factor}：${x.detail}（${x.candidate_code}）` })
  }
  return rows
})

function bandType(b) { return b === 'high' ? 'danger' : b === 'mid' ? 'warning' : 'info' }
function bandLabel(b) { return { high: '高分段', mid: '中分段', low: '低分段' }[b] || b }
function sourceType(s) { return { historical: 'warning', random_audit: 'primary', none: 'info' }[s] || 'info' }
function sourceLabel(s) { return { historical: '历史核查', random_audit: '随机复核', none: '未核查' }[s] || s }

async function loadAll() {
  batch.value = await api.get(`/api/hitrate/batches/${batchCode.value}`)
  await Promise.all([loadAccounting(), loadCandidates(), loadRuns()])
}
async function loadAccounting() {
  accounting.value = await api.get(`/api/hitrate/batches/${batchCode.value}/accounting`)
}
async function loadCandidates() {
  const params = {}
  if (fRegion.value) params.region = fRegion.value
  if (fCensor.value) params.censor_status = fCensor.value
  if (fSource.value) params.review_source = fSource.value
  candidates.value = (await api.get(`/api/hitrate/batches/${batchCode.value}/candidates`, { params })).items
}
async function loadRuns() {
  runs.value = (await api.get('/api/hitrate/runs', { params: { batch_code: batchCode.value } })).items
  if (runs.value.length) run.value = await api.get(`/api/hitrate/runs/${runs.value[0].id}`)
}
async function reevaluate() {
  evaluating.value = true
  try {
    const r = await api.post(`/api/hitrate/batches/${batchCode.value}/evaluate`, { note: '界面手动触发双路评估' })
    ElMessage.success('评估完成，已留痕')
    await Promise.all([loadAccounting(), loadRuns()])
    return r
  } finally { evaluating.value = false }
}
function openAudit(row) {
  auditRow.value = row
  auditForm.value = { evidence_label: 'confirmed', evidence_code: '' }
  auditDlg.value = true
}
async function submitAudit() {
  savingAudit.value = true
  try {
    await api.post(`/api/hitrate/candidates/${auditRow.value.id}/audit`, {
      evidence_label: auditForm.value.evidence_label,
      evidence_codes: auditForm.value.evidence_code ? [auditForm.value.evidence_code] : [],
    })
    ElMessage.success('复核已登记，入样概率已更新；请重新运行评估')
    auditDlg.value = false
    await Promise.all([loadAccounting(), loadCandidates(), loadRuns()])
  } finally { savingAudit.value = false }
}

onMounted(async () => {
  batches.value = (await api.get('/api/hitrate/batches')).items
  if (batches.value.length) {
    batchCode.value = batches.value[0].code
    await loadAll()
  }
})
</script>

<style scoped>
.estimate-card { min-height: 330px; }
.big-number { font-size: 38px; font-weight: 800; color: var(--rg-blue); line-height: 1.2; }
.formula { background: #f1f5f9; border-radius: 6px; padding: 6px 10px; margin-top: 10px; font-size: 12px; color: #334155; }
.block-line { display: flex; gap: 8px; align-items: flex-start; margin-top: 8px; line-height: 1.6; }
.naive-rate { font-size: 20px; color: var(--rg-muted); }
.ml { margin-left: 6px; }
.mb8 { margin-bottom: 8px; }
</style>
