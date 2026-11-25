<template>
  <div class="login-bg">
    <el-card class="login-card">
      <div class="brand">
        <el-icon :size="30" color="#1d4ed8"><Connection /></el-icon>
        <h1>医保风险线索关系分析平台</h1>
      </div>
      <p class="sub">来源账 · 分级身份解析 · 受限图扩展 · 模式求值 · 反证编排 · 快照复演</p>
      <el-form @submit.prevent="login">
        <el-form-item>
          <el-input v-model="username" size="large" placeholder="用户名" :prefix-icon="User" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" size="large" type="password" show-password
                    placeholder="密码" :prefix-icon="Lock" @keyup.enter="login" />
        </el-form-item>
        <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="login">
          登录
        </el-button>
      </el-form>
      <el-alert class="mt16" type="info" :closable="false">
        <div class="small">测试账号：<b>admin / admin123</b>（全部权限）</div>
        <div class="small">稽核员：<b>auditor / audit123</b>（仅朝阳片区，可验证投影权限）</div>
      </el-alert>
      <p class="disclaimer">平台评分仅为风险线索优先级，不构成欺诈或违法结论。</p>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import api from '../api.js'
import { store } from '../store.js'
import { ElMessage } from 'element-plus'

const username = ref('admin'), password = ref('admin123'), loading = ref(false)
const router = useRouter()
async function login() {
  loading.value = true
  try {
    const r = await api.post('/api/auth/login', { username: username.value, password: password.value })
    store.setAuth(r.token, r.user)
    ElMessage.success(`欢迎，${r.user.display_name}`)
    router.push('/dashboard')
  } finally { loading.value = false }
}
</script>

<style scoped>
.login-bg { height: 100%; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0e1f3d 0%, #1d4ed8 100%); }
.login-card { width: 460px; padding: 22px 18px 10px; border-radius: 16px; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand h1 { font-size: 20px; margin: 0; }
.sub { color: #64748b; font-size: 12.5px; line-height: 1.7; margin: 12px 0 20px; }
.disclaimer { text-align: center; color: #b45309; font-size: 12px; margin: 14px 0 6px; }
</style>
