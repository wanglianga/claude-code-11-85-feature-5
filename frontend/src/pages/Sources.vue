<template>
  <div class="page">
    <h2 class="page-title">来源账</h2>
    <p class="page-sub">
      每条业务记录都可回溯到来源系统、业务有效时间、接收时间、可引用字段，以及撤销/更正链。
    </p>

    <div class="card">
      <div class="flex gap8" style="flex-wrap:wrap">
        <el-select v-model="filters.source_system" placeholder="来源系统" clearable style="width:210px" @change="load">
          <el-option v-for="s in meta.systems" :key="s" :label="s" :value="s" />
        </el-select>
        <el-select v-model="filters.record_type" placeholder="记录类型" clearable style="width:170px" @change="load">
          <el-option v-for="t in meta.types" :key="t" :label="t" :value="t" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable style="width:130px" @change="load">
          <el-option label="当前 current" value="current" />
          <el-option label="被更正 corrected" value="corrected" />
          <el-option label="已撤销 revoked" value="revoked" />
        </el-select>
        <el-input v-model="filters.q" placeholder="搜索 code / 业务键 / 引用字段" clearable style="width:260px"
                  @keyup.enter="load" />
        <el-button type="primary" @click="load">查询</el-button>
      </div>

      <el-table :data="rows" size="small" class="mt12" border height="600" @row-click="open">
        <el-table-column prop="code" label="来源编号" width="150" class-name="mono" />
        <el-table-column prop="source_system" label="来源系统" width="150" />
        <el-table-column prop="record_type" label="类型" width="150" />
        <el-table-column prop="citation" label="可引用字段（凭证/单据/台账）" min-width="260" show-overflow-tooltip />
        <el-table-column label="业务有效时间" width="170">
          <template #default="{ row }">{{ fmt(row.valid_from) }}</template>
        </el-table-column>
        <el-table-column label="接收时间" width="170">
          <template #default="{ row }">{{ fmt(row.received_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'current' ? 'success' : 'info'">
              {{ { current: '当前', corrected: '被更正', revoked: '已撤销' }[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="drawer" size="560px" :title="detail?.code">
      <template v-if="detail">
        <div class="kv">
          <div class="k">来源系统</div><div>{{ detail.source_system }}</div>
          <div class="k">记录类型</div><div>{{ detail.record_type }}</div>
          <div class="k">业务键</div><div class="mono">{{ detail.business_key }}</div>
          <div class="k">引用字段</div><div>{{ detail.citation }}</div>
          <div class="k">业务生效</div><div>{{ fmt(detail.valid_from) }} ~ {{ fmt(detail.valid_to) || '长期' }}</div>
          <div class="k">接收入库</div><div>{{ fmt(detail.received_at) }}</div>
          <div class="k">状态</div><div>{{ detail.status }}</div>
          <div v-if="detail.correction_reason" class="k">更正原因</div>
          <div v-if="detail.correction_reason">{{ detail.correction_reason }}</div>
        </div>

        <div v-if="detail.supersedes" class="chain-step mt12">
          ⬅ 本记录更正/接续：<b>{{ detail.supersedes.code }}</b>（{{ detail.supersedes.status }}）
          <div class="small muted">{{ detail.supersedes.citation }}</div>
        </div>
        <div v-if="detail.superseded_by" class="chain-step refute mt8">
          ➡ 已被新记录更正：<b>{{ detail.superseded_by.code }}</b>
          <div class="small muted">{{ detail.superseded_by.correction_reason }}</div>
        </div>

        <h4 class="mt16">原始载荷 payload</h4>
        <pre class="detail-pre">{{ JSON.stringify(detail.payload, null, 2) }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api.js'

const route = useRoute()
const rows = ref([])
const meta = ref({ systems: [], types: [] })
const filters = reactive({ source_system: '', record_type: '', status: '', q: '' })
const drawer = ref(false)
const detail = ref(null)

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 16) : '' }
async function load() {
  const r = await api.get('/api/sources', { params: { ...filters } })
  rows.value = r.items
  meta.value = { systems: r.systems, types: r.types }
}
async function open(row) {
  detail.value = await api.get(`/api/sources/${row.code}`)
  drawer.value = true
}
onMounted(() => {
  if (route.query.q) filters.q = String(route.query.q)
  load()
})
</script>
