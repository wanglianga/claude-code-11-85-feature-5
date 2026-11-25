<template>
  <div class="page" v-if="detail">
    <div class="flex-between">
      <div>
        <el-button link @click="$router.push('/cases')"><el-icon><ArrowLeft /></el-icon> 返回列表</el-button>
        <h2 class="page-title" style="display:inline;margin-left:8px">{{ detail.case.code }} · {{ detail.case.title }}</h2>
      </div>
      <el-tag :type="STATUS_TYPE[detail.case.status] || 'info'" size="large">
        {{ detail.case.status_label }}
      </el-tag>
    </div>

    <div class="disclaimer-banner mt8">⚖️ {{ judgment.disclaimer || current.disclaimer }}</div>
    <el-alert v-if="detail.case.affected" class="mb8" type="error" show-icon
              :title="`该案件受后续变更影响：${detail.case.affected_reason}。当前展示的最新判断已重算；v1 等旧判断仍保留原快照与来源。`" />

    <div class="card">
      <div class="flex" style="flex-wrap:wrap">
        <span class="small muted">人工核查状态：</span>
        <el-select v-model="newStatus" style="width:210px" size="small">
          <el-option v-for="(label, key) in detail.status_labels" :key="key" :label="label" :value="key" />
        </el-select>
        <el-input v-model="reviewNote" size="small" style="width:340px" placeholder="核查意见（写入审计时间线）" />
        <el-button size="small" type="primary" @click="submitReview">提交核查状态</el-button>
        <el-tag v-if="detail.case.assignee" size="small" type="info">经办人：{{ detail.case.assignee }}</el-tag>
      </div>
    </div>

    <el-tabs v-model="tab">
      <!-- 投影 -->
      <el-tab-pane label="最小投影 / 支持链" name="graph">
        <el-row :gutter="14">
          <el-col :span="17">
            <div class="card">
              <div class="flex-between mb8">
                <h3 style="margin:0">有权限的最小子图（判断 v{{ judgment.version }} · 快照 #{{ judgment.snapshot_no }}）</h3>
                <el-checkbox v-model="highlight">高亮有序支持链</el-checkbox>
              </div>
              <GraphCanvas :graph="judgment.projection"
                           :highlight-uids="highlight ? judgment.projection.ordered_chain_uids : []"
                           @select-edge="selEdge = $event" />
            </div>
          </el-col>
          <el-col :span="7">
            <div class="card">
              <h3>💰 金额范围</h3>
              <div class="kv">
                <div class="k">基金支出下限</div><div><b>{{ yuan(amt.min) }}</b></div>
                <div class="k">总费用上限</div><div><b>{{ yuan(amt.max) }}</b></div>
                <div class="k">申报总额</div><div>{{ yuan(amt.total_claimed) }}</div>
                <div class="k">申报基金</div><div>{{ yuan(amt.fund_claimed) }}</div>
              </div>
              <p class="small muted mt8">下限按可认定基金暴露（剔除已更正/退回），上限为关联结算总费用；仅作核查金额区间。</p>
            </div>
            <div class="card">
              <h3>🧭 有序支持链（规则与图模型一致）</h3>
              <div v-for="(uid, i) in judgment.rule_result.support_chain || []" :key="uid">
                <div class="chain-step" :class="{ negated: edgeOf(uid)?.status === 'negated' }">
                  <b>{{ i + 1 }}.</b> {{ edgeOf(uid)?.basis || uid }}
                  <div class="small muted">
                    {{ edgeLabel(edgeOf(uid)?.edge_type) }}
                    <span v-if="edgeOf(uid)?.source_codes?.length">
                      · 出处 {{ edgeOf(uid).source_codes.join('，') }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div class="card" v-if="selEdge">
              <h3>边依据</h3>
              <p class="small">{{ selEdge.basis }}</p>
              <div class="mono small muted">{{ selEdge.uid }}</div>
            </div>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- 规则事实 -->
      <el-tab-pane label="模式必要事实 / 规则" name="rule">
        <div class="card">
          <h3>{{ judgment.rule_result.pattern_name }}
            <el-tag :type="judgment.rule_result.fired ? 'danger' : 'success'" size="small">
              {{ judgment.rule_result.fired ? '结构命中' : '未命中/排除' }}
            </el-tag>
            <span class="muted small">原始规则分 {{ judgment.rule_result.raw_score }}</span>
          </h3>
          <el-table :data="judgment.rule_result.necessary_facts" size="small" border>
            <el-table-column prop="label" label="必要事实" min-width="220" />
            <el-table-column label="判定" width="90" align="center">
              <template #default="{ row }">
                <span :class="row.passed ? 'fact-pass' : 'fact-fail'">{{ row.passed ? '成立' : '不成立' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="值" width="180">
              <template #default="{ row }"><span class="mono small">{{ formatVal(row.value) }}</span></template>
            </el-table-column>
            <el-table-column prop="detail" label="说明" min-width="260" show-overflow-tooltip />
          </el-table>
          <h3 class="mt16">可审计加分项</h3>
          <div v-for="c in judgment.rule_result.factor_contributions" :key="c.factor" class="chain-step">
            {{ c.label }} <b style="float:right">+{{ c.add }}</b>
          </div>
          <el-empty v-if="!(judgment.rule_result.factor_contributions || []).length" description="无加分项（结构未命中）" />
        </div>
      </el-tab-pane>

      <!-- 图模型 -->
      <el-tab-pane label="图模型解释" name="model" v-if="judgment.model_result">
        <div class="card">
          <div class="flex gap8">
            <div class="stat" style="flex:1"><div class="n">{{ Math.round(judgment.model_result.final_score ?? judgment.model_result.calibrated_score) }}</div><div class="l">最终排序分（含反证闸门）</div></div>
            <div class="stat" style="flex:1"><div class="n" style="color:#64748b">{{ Math.round(judgment.model_result.ml_score ?? judgment.model_result.calibrated_score) }}</div><div class="l">校准后 ML 分（闸门前）</div></div>
            <div class="stat" style="flex:1"><div class="n" style="color:#94a3b8">{{ Math.round(judgment.model_result.raw_score) }}</div><div class="l">raw 原始分</div></div>
            <div class="stat" style="flex:2">
              <div class="l">偏差校准</div>
              <div class="small mt8">地区 {{ judgment.model_result.calibration_applied.region }} ×{{ judgment.model_result.calibration_applied.region_factor }}；
                机构类型 {{ judgment.model_result.calibration_applied.org_type }} ×{{ judgment.model_result.calibration_applied.org_factor }}；
                总因子 ×{{ judgment.model_result.calibration_applied.total_factor }}</div>
            </div>
          </div>
          <el-alert v-if="judgment.model_result.counter_gate?.applied" class="mt12" type="success" show-icon
                    :title="`反证闸门生效：${judgment.model_result.counter_gate.close_checks.map(x=>x.title).join('、')} 已取得关闭级结论，模型排序分封顶 ${judgment.model_result.counter_gate.cap}。`"
                    :description="judgment.model_result.counter_gate.reason" />
          <el-alert class="mt12" type="success" :closable="false"
                    :title="`可追溯路径一致性：模型 trace_chain_uids 与规则 support_chain ${chainsEqual ? '完全相同 ✔' : '不一致'}`" />
          <h3 class="mt16">Top 特征贡献</h3>
          <el-table :data="judgment.model_result.top_contributions" size="small" border>
            <el-table-column prop="label" label="特征" min-width="180" />
            <el-table-column label="取值" width="100">
              <template #default="{ row }">{{ row.value }}</template>
            </el-table-column>
            <el-table-column label="权重" width="100">
              <template #default="{ row }">{{ row.weight }}</template>
            </el-table-column>
            <el-table-column label="贡献" width="120">
              <template #default="{ row }">
                <b :style="{ color: row.contribution >= 0 ? '#dc2626' : '#15803d' }">
                  {{ row.contribution >= 0 ? '+' : '' }}{{ row.contribution }}
                </b>
              </template>
            </el-table-column>
          </el-table>
          <el-alert class="mt12" type="warning" :closable="false" :title="judgment.model_result.fairness_note" />
          <p class="small muted mt8">{{ judgment.model_result.disclaimer }}</p>
        </div>
      </el-tab-pane>

      <!-- 反证 -->
      <el-tab-pane :label="`反证联查（${judgment.counter_result.checks?.length || 0}）`" name="counter">
        <div class="card">
          <div class="flex gap8">
            <el-progress type="dashboard" :percentage="Math.round((judgment.counter_result.completeness || 0) * 100)"
                         :width="110" />
            <div>
              <h3 style="margin:0">反证完整度</h3>
              <p class="small muted" style="max-width:520px">
                形成结论性/合理解释的核查渠道占比；未排除项 {{ judgment.counter_result.unresolved }}。
                编排器联查进销存、签收、停诊、床位、政策、申诉六类材料，明确“哪些解释仍未排除”。
              </p>
            </div>
          </div>
          <el-row :gutter="12" class="mt12">
            <el-col :span="12" v-for="c in judgment.counter_result.checks" :key="c.check_type">
              <el-card shadow="never" class="mb8">
                <div class="flex-between">
                  <b>{{ c.title }}</b>
                  <el-tag size="small" :type="checkType(c.status)">{{ checkLabel(c.status) }}</el-tag>
                </div>
                <p class="small" style="line-height:1.7;margin:8px 0">{{ c.detail }}</p>
                <div v-if="c.source_codes?.length" class="small">
                  <span class="muted">出处：</span>
                  <router-link v-for="s in c.source_codes" :key="s" :to="`/sources?q=${s}`"
                               class="chain-uid" style="display:inline-block;margin:2px 4px 0 0">{{ s }}</router-link>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </div>
      </el-tab-pane>

      <!-- 版本与核查轨迹 -->
      <el-tab-pane label="判断版本 / 核查轨迹" name="versions">
        <div class="card">
          <h3>不可变判断版本</h3>
          <el-table :data="detail.versions" size="small" border>
            <el-table-column prop="version" label="版本" width="70" />
            <el-table-column prop="kind" label="类型" width="110" />
            <el-table-column prop="author" label="产生者" width="120" />
            <el-table-column prop="snapshot_no" label="冻结快照" width="90" />
            <el-table-column prop="note" label="说明" min-width="260" show-overflow-tooltip />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="loadVersion(row.version)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-button v-if="viewingVersion" class="mt12" size="small" @click="judgment = current; viewingVersion = null">
            返回当前版本 v{{ current.version }}
          </el-button>

          <h3 class="mt16">人工核查时间线</h3>
          <el-timeline class="mt8">
            <el-timeline-item v-for="(e, i) in detail.review_events" :key="i"
                              :timestamp="fmt(e.at)" placement="top">
              <b>{{ e.reviewer }}</b>：{{ e.from_status || '—' }} → {{ e.to_status }}
              <div class="small muted">{{ e.note }}</div>
            </el-timeline-item>
            <div v-if="!detail.review_events.length" class="muted small">暂无人工状态变更。</div>
          </el-timeline>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api.js'
import GraphCanvas from '../components/GraphCanvas.vue'
import { EDGE_META, STATUS_TYPE, yuan } from '../store.js'
import { ElMessage } from 'element-plus'

const route = useRoute()
const code = route.params.code
const detail = ref(null)
const current = ref(null)
const judgment = ref({})
const tab = ref('graph')
const highlight = ref(true)
const selEdge = ref(null)
const newStatus = ref('')
const reviewNote = ref('')
const viewingVersion = ref(null)

const amt = computed(() => judgment.value.amount_range || {})
const chainsEqual = computed(() => JSON.stringify(judgment.value.model_result?.trace_chain_uids)
  === JSON.stringify(judgment.value.rule_result?.support_chain))

function edgeOf(uid) { return (judgment.value.projection?.edges || []).find((e) => e.uid === uid) }
function edgeLabel(t) { return EDGE_META[t]?.label || t }
function formatVal(v) { return typeof v === 'object' ? JSON.stringify(v) : String(v) }
function fmt(s) { return s ? s.replace('T', ' ').slice(0, 16) : '' }
function checkType(s) {
  return { checked_explains: 'success', confirmed: 'success', not_excluded: 'danger', no_record: 'info' }[s]
}
function checkLabel(s) {
  return { checked_explains: '存在合理解释', confirmed: '反证确认成立', not_excluded: '仍未排除', no_record: '未查到材料' }[s] || s
}

async function load() {
  detail.value = await api.get(`/api/cases/${code}`)
  current.value = detail.value.current_judgment
  judgment.value = current.value
  newStatus.value = detail.value.case.status
}
async function loadVersion(v) {
  judgment.value = await api.get(`/api/cases/${code}/judgments/${v}`)
  viewingVersion.value = v
  ElMessage.info(`正在查看不可变历史判断 v${v}`)
}
async function submitReview() {
  await api.post(`/api/cases/${code}/review`, { to_status: newStatus.value, note: reviewNote.value })
  ElMessage.success('核查状态已保存')
  reviewNote.value = ''
  load()
}
onMounted(load)
</script>
