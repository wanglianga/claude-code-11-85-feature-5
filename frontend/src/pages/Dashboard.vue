<template>
  <div class="page">
    <h2 class="page-title">总览与虚构网络验收剧本</h2>
    <p class="page-sub">
      所有数据均为虚构，覆盖题目要求的四类验收原型。建议先运行“一键验收”，再按下方导航逐项查看实现。
    </p>

    <div class="disclaimer-banner">
      ⚖️ 平台输出（规则分 / 图模型分 / 排序）仅表示风险结构与核查优先级，<b>不构成欺诈或违法结论</b>；
      最终定性须经人工核查与法定程序。家庭共用联系方式、养老机构代办、慢病协作不会被直接解释为欺诈。
    </div>

    <div class="grid stat-grid">
      <div class="stat"><div class="n">{{ stats.persons ?? '-' }}</div><div class="l">虚构自然人/档案</div></div>
      <div class="stat"><div class="n">{{ stats.source_records ?? '-' }}</div><div class="l">来源账记录</div></div>
      <div class="stat"><div class="n">{{ stats.nodes ?? '-' }}</div><div class="l">当前图节点</div></div>
      <div class="stat"><div class="n">{{ stats.edges ?? '-' }}</div><div class="l">图边（否定 {{ stats.negated_edges ?? 0 }}）</div></div>
      <div class="stat"><div class="n">{{ stats.cases ?? '-' }}</div><div class="l">案件/候选</div></div>
      <div class="stat"><div class="n" style="color:#b45309">{{ stats.weak_pending ?? '-' }}</div><div class="l">待核弱身份候选</div></div>
      <div class="stat"><div class="n">{{ stats.snapshots ?? '-' }}</div><div class="l">冻结快照</div></div>
    </div>

    <div class="card mt16">
      <div class="flex-between">
        <h3 style="margin:0">🧪 一键验收（服务端断言，只读）</h3>
        <div class="flex gap8">
          <el-button type="primary" :loading="running" @click="run">运行验收</el-button>
          <el-popconfirm v-if="store.isAdmin" title="将清空并重建全部演示数据，确定？" @confirm="reset">
            <template #reference><el-button type="danger" plain>重置演示数据</el-button></template>
          </el-popconfirm>
        </div>
      </div>

      <el-result v-if="result" :icon="result.overall ? 'success' : 'error'"
                  :title="result.overall ? `全部通过 ${result.passed}/${result.total}` : `存在未通过项 ${result.passed}/${result.total}`"
                  style="padding: 12px 0" />
      <el-table v-if="result" :data="result.checks" size="small" border>
        <el-table-column width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.passed ? 'success' : 'danger'" size="small">{{ row.passed ? 'PASS' : 'FAIL' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="验收点" min-width="320" />
        <el-table-column label="实际值" min-width="260">
          <template #default="{ row }">
            <span class="mono small">{{ JSON.stringify(row.actual) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card">
      <h3>📖 验收剧本导航</h3>
      <el-steps direction="vertical" :active="9" finish-status="success">
        <el-step title="P1 多点重复购药（C-001）"
                 description="伪标识 P1：两药店同日同成分（氨氯地平）、28 天数量重叠，医师陈明当日停诊 SCH1，两结算均在同一给药期。规则与图模型给出完全相同的支持链。">
        </el-step>
        <el-step title="P2/P3 养老机构电话（C-002，弱身份）"
                 description="共用 010-88886666 与代办社工，仅生成 pending 弱边，不并人、不沿 P3 扩展；但各自处方与本人签收匹配、床位记录在保。" />
        <el-step title="P4 药店间调拨（C-003，合法供药）"
                 description="表面重复由调拨单 TR4 + 串户红冲更正解释，进销存闭环；重叠边置 negated，不再用于扩展。" />
        <el-step title="P5 申诉确认退回（C-004，关闭）"
                 description="APP1 申诉成立、退货验收单 TH-0318 与 return_in 台账印证，案件 closed_returned。" />
        <el-step title="扩展场景"
                 description="C-005 拆方、C-006 集中结算、C-007 异常转诊（¥88,200 高金额跨机构扩展门槛）、C-008 慢病协作不命中。" />
      </el-steps>
      <div class="flex gap8 mt12">
        <el-button @click="$router.push('/patterns')">前往模式排序</el-button>
        <el-button @click="$router.push('/explorer?case=C-001')">在图扩展中查看 P1</el-button>
        <el-button @click="$router.push('/snapshots')">快照复演 / 触发变更</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../api.js'
import { store } from '../store.js'
import { ElMessage } from 'element-plus'

const stats = ref({})
const result = ref(null)
const running = ref(false)

async function loadStats() { stats.value = await api.get('/api/meta/stats') }
async function run() {
  running.value = true
  try { result.value = await api.get('/api/acceptance/run') } finally { running.value = false }
}
async function reset() {
  await api.post('/api/meta/reset')
  ElMessage.success('已重置')
  result.value = null
  loadStats()
}
onMounted(loadStats)
</script>
