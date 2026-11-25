<template>
  <div class="page">
    <h2 class="page-title">模式求值与候选排序</h2>
    <p class="page-sub">
      同一调查排序目标（模式）下：可审计规则组合先筛选风险结构，再按金额与反证完整度排序；
      图模型使用带时间切分验证的逻辑回归对同一候选集排序，并校准地区/机构偏差。两者在 C-001 上共享同一条支持链。
    </p>

    <div class="card">
      <div class="flex-between">
        <div class="flex gap8">
          <span class="muted small">排序目标：</span>
          <el-select v-model="pattern" style="width:200px" @change="loadRank">
            <el-option label="多点重复购药" value="MULTI_POINT" />
            <el-option label="拆方" value="SPLIT_RX" />
            <el-option label="集中结算" value="CENTRAL_SETTLE" />
            <el-option label="异常转诊" value="ABNORMAL_REFERRAL" />
            <el-option label="弱身份候选" value="WEAK_IDENTITY" />
            <el-option label="全部案件" value="ALL" />
          </el-select>
          <el-radio-group v-model="sort" @change="loadRank">
            <el-radio-button value="amount_completeness">规则·金额→反证完整度</el-radio-button>
            <el-radio-button value="amount_unresolved">金额→未排除项</el-radio-button>
            <el-radio-button value="rule">规则分</el-radio-button>
            <el-radio-button value="model">图模型分</el-radio-button>
          </el-radio-group>
        </div>
        <el-button type="primary" plain :loading="evaluating" @click="reevaluate">重新运行全部求值</el-button>
      </div>
      <el-alert class="mt12" type="info" :title="rankHelp" :closable="false" />

      <el-table :data="rows" size="small" class="mt12" border @row-click="openCase" style="cursor:pointer">
        <el-table-column prop="rank" label="#" width="46" align="center" />
        <el-table-column prop="code" label="案件" width="86" />
        <el-table-column prop="title" label="线索" min-width="280" show-overflow-tooltip />
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="STATUS_TYPE[row.status] || 'info'">{{ statusLabel(row.status) }}</el-tag>
            <el-tag v-if="row.affected" size="small" type="danger" effect="dark" class="ml">变更影响</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额区间（基金下限~总费用上限）" width="230" sortable :sort-method="() => 0">
          <template #default="{ row }">
            <span class="small">{{ yuan(row.amount_min) }} ~ {{ yuan(row.amount_max) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="反证完整度" width="150">
          <template #default="{ row }">
            <el-progress :percentage="Math.round(row.counter_completeness * 100)" :stroke-width="10"
                         :status="row.counter_completeness >= 0.6 ? 'success' : ''" />
            <span class="small muted">未排除 {{ row.unresolved_counter }} 项</span>
          </template>
        </el-table-column>
        <el-table-column label="规则分(生效)" width="100" align="center">
          <template #default="{ row }">
            <b>{{ Math.round(row.rule_score_effective) }}</b>
            <span class="small muted"> / {{ Math.round(row.rule_score_raw) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="模型分" width="90" align="center">
          <template #default="{ row }">
            <b :style="{ color: (row.model_score || 0) >= 60 ? '#dc2626' : '#334155' }">
              {{ row.model_score != null ? Math.round(row.model_score) : '—' }}
            </b>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-tabs v-model="tab" class="card">
      <el-tab-pane label="模式配置（时间窗/边范围/高金额扩展）" name="configs">
        <el-row :gutter="12">
          <el-col :span="12" v-for="c in configs" :key="c.code" class="mb8">
            <el-card shadow="never">
              <div class="flex-between">
                <b>{{ c.name }} <span class="muted mono small">{{ c.code }}</span></b>
                <el-tag size="small" :type="c.enabled ? 'success' : 'info'">{{ c.enabled ? '启用' : '停用' }}</el-tag>
              </div>
              <p class="small" style="line-height:1.7">{{ c.description }}</p>
              <div class="small kv">
                <div class="k">时间窗/跳数</div><div>{{ c.window_days }} 天 · {{ c.max_hops }} 跳</div>
                <div class="k">允许边</div><div>{{ c.allowed_edge_types.join('、') }}</div>
                <div class="k">参数</div><div class="mono">{{ JSON.stringify(c.params) }}</div>
                <div class="k">政策版本</div><div>{{ c.policy_code }}@{{ c.policy_version }}</div>
                <div class="k">高金额门槛</div><div>{{ yuan(c.high_amount_threshold) }}</div>
                <div class="k">扩展边/截止</div>
                <div>{{ c.extended_edge_types?.join('、') || '—' }}<br />
                  <span class="muted">{{ c.extension_cutoff || '' }}</span></div>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <el-tab-pane label="图模型：时间切分验证 / 校准 / 权重" name="model">
        <div v-if="model.trained">
          <el-row :gutter="14">
            <el-col :span="14">
              <h3>验证集指标（2026-02 业务日期，训练截止 {{ model.train_end }}）</h3>
              <div class="grid stat-grid">
                <div class="stat"><div class="n">{{ model.metrics.auc }}</div><div class="l">AUC</div></div>
                <div class="stat"><div class="n">{{ model.metrics.f1 }}</div><div class="l">F1</div></div>
                <div class="stat"><div class="n">{{ model.metrics.precision }}</div><div class="l">精确率</div></div>
                <div class="stat"><div class="n">{{ model.metrics.recall }}</div><div class="l">模型召回</div></div>
                <div class="stat"><div class="n" style="color:#b45309">{{ model.metrics.rule_baseline_recall }}</div><div class="l">规则基线召回</div></div>
                <div class="stat"><div class="n" style="color:#15803d">+{{ model.metrics.recall_gain_vs_rules }}</div><div class="l">召回提升</div></div>
              </div>
              <p class="small muted mt8">{{ model.metrics.split }}；训练 {{ model.metrics.train_size }} / 验证 {{ model.metrics.val_size }}。
                模型对新型协作模式可能有更高召回，但也必须接受偏差校准与人工复核。</p>
              <h3 class="mt16">地区 / 机构类型校准因子</h3>
              <pre class="detail-pre">{{ JSON.stringify(model.calibration, null, 2) }}</pre>
            </el-col>
            <el-col :span="10">
              <h3>可审计特征权重</h3>
              <el-table :data="weightRows" size="small" border max-height="430">
                <el-table-column prop="label" label="特征" />
                <el-table-column prop="w" label="权重" width="100" align="right">
                  <template #default="{ row }">
                    <span :style="{ color: row.w >= 0 ? '#dc2626' : '#15803d', fontWeight: 700 }">
                      {{ row.w >= 0 ? '+' : '' }}{{ row.w }}
                    </span>
                  </template>
                </el-table-column>
              </el-table>
              <el-popconfirm title="使用演练样本重新训练？" @confirm="retrain">
                <template #reference><el-button type="primary" plain class="mt12">重新训练（管理员）</el-button></template>
              </el-popconfirm>
              <p class="small muted mt8">{{ model.note }}</p>
            </el-col>
          </el-row>
        </div>
        <el-empty v-else description="模型尚未训练" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api.js'
import { STATUS_TYPE, yuan } from '../store.js'
import { ElMessage } from 'element-plus'

const router = useRouter()
const pattern = ref('MULTI_POINT')
const sort = ref('amount_completeness')
const rows = ref([])
const configs = ref([])
const model = ref({ trained: false })
const tab = ref('configs')
const evaluating = ref(false)

const rankHelp = computed(() => rows.value.length ? '排序说明：' + (rows.value[0] ? '' : '') + helpText.value : '')
const helpText = ref('规则结构命中后，按金额上限↓、反证完整度↓排序')
const weightRows = computed(() => Object.entries(model.value.weights || {})
  .filter(([k]) => k !== 'bias').map(([k, w]) => ({ k, label: featureLabel(k), w }))
  .sort((a, b) => b.w - a.w))

const FEATURE_LABELS = {
  strong_cluster: '同伪标识/强簇', weak_identity_only: '仅弱身份', distinct_pharmacies: '跨药店数',
  same_day: '同日', overlap_days: '重叠天数', overlap_qty: '重叠数量', schedule_conflict: '停诊冲突',
  split_group: '拆方', central_group: '集中结算', referral_group: '异常转诊', cross_org: '跨机构',
  log_amount: '金额规模', high_amount: '高金额', unresolved: '未排除反证', counter_explains: '反证解释',
  negated_ratio: '否定边比例', signature_match: '签收匹配', bed_match: '床位记录',
  policy_compliant: '政策合规', appeal_return: '申诉退回'
}
function featureLabel(k) { return FEATURE_LABELS[k] || k }
function statusLabel(s) {
  return { pending: '待核查', reviewing: '核查中', new: '新线索', confirmed_risk_clue: '风险线索成立',
    excluded_legitimate: '已排除·合法', closed_returned: '关闭·退回', weak_identity: '弱身份待核' }[s] || s
}
async function loadRank() {
  const r = await api.get('/api/patterns/ranking', { params: { pattern_code: pattern.value, sort: sort.value } })
  rows.value = r.items
  helpText.value = r.sort_help[sort.value]
}
function openCase(row) { router.push(`/cases/${row.code}`) }
async function reevaluate() {
  evaluating.value = true
  try {
    const r = await api.post('/api/patterns/evaluate', {})
    ElMessage.success(`已对 ${r.rejudged.length} 个案件追加新判断版本`)
    loadRank()
  } finally { evaluating.value = false }
}
async function retrain() {
  await api.post('/api/patterns/model/train')
  ElMessage.success('训练完成')
  model.value = await api.get('/api/patterns/model/latest')
}
onMounted(async () => {
  loadRank()
  configs.value = (await api.get('/api/patterns/configs')).items
  model.value = await api.get('/api/patterns/model/latest')
})
</script>
