<template>
  <div class="page">
    <h2 class="page-title">案件投影</h2>
    <p class="page-sub">
      投影只呈现核查所需的<b>有权限的最小子图、金额范围与证据出处</b>，并保留人工核查状态。
      分数不是违法结论。auditor 账号仅能看到朝阳片区案件，可用于验证权限收敛。
    </p>
    <div class="card">
      <div class="flex gap8 mb8" style="flex-wrap:wrap">
        <el-radio-group v-model="statusFilter" @change="filter">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="open">进行中/待核</el-radio-button>
          <el-radio-button value="closed">已排除/关闭</el-radio-button>
          <el-radio-button value="weak_identity">弱身份</el-radio-button>
        </el-radio-group>
        <el-input v-model="kw" placeholder="搜索编号/标题/人员" style="width:260px" clearable @input="filter" />
      </div>
      <el-table :data="shown" border size="small" @row-click="open" style="cursor:pointer">
        <el-table-column prop="code" label="编号" width="86" />
        <el-table-column prop="title" label="标题" min-width="300" show-overflow-tooltip />
        <el-table-column label="模式" width="170">
          <template #default="{ row }">
            <el-tag v-for="p in row.pattern_codes" :key="p" size="small" class="mr4">{{ p }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="region" label="统筹区" width="80" />
        <el-table-column label="金额区间" width="210">
          <template #default="{ row }" class="small">{{ yuan(row.amount_min) }} ~ {{ yuan(row.amount_max) }}</template>
        </el-table-column>
        <el-table-column label="完整度" width="120">
          <template #default="{ row }">{{ Math.round(row.counter_completeness * 100) }}%（{{ row.unresolved_counter }} 未排除）</template>
        </el-table-column>
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag size="small" :type="STATUS_TYPE[row.status] || 'info'">{{ row.status_label }}</el-tag>
            <el-tag v-if="row.affected" size="small" type="danger" effect="dark" class="ml">快照变更</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api.js'
import { STATUS_TYPE, yuan } from '../store.js'

const cases = ref([])
const statusFilter = ref('')
const kw = ref('')
const router = useRouter()

const CLOSED = ['excluded_legitimate', 'closed_returned', 'closed_weak_identity']
const shown = computed(() => cases.value.filter((c) => {
  if (statusFilter.value === 'open' && (CLOSED.includes(c.status) || c.status === 'weak_identity')) return false
  if (statusFilter.value === 'closed' && !CLOSED.includes(c.status)) return false
  if (statusFilter.value === 'weak_identity' && c.status !== 'weak_identity') return false
  if (kw.value) {
    const k = kw.value.toLowerCase()
    return [c.code, c.title, c.primary_person_code].join(' ').toLowerCase().includes(k)
  }
  return true
}))
function filter() {}
function open(row) { router.push(`/cases/${row.code}`) }
onMounted(async () => { cases.value = (await api.get('/api/cases')).items })
</script>
<style scoped>.mr4 { margin-right: 4px; } .ml { margin-left: 4px; }</style>
