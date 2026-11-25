<template>
  <el-container style="height: 100%">
    <el-aside width="232px" class="aside">
      <div class="logo">
        <el-icon :size="22"><Connection /></el-icon>
        <div>
          <div class="logo-title">风险线索关系分析</div>
          <div class="logo-sub">医保稽核 · 受限图平台</div>
        </div>
      </div>
      <el-menu :default-active="$route.path" router class="menu" background-color="#0e1f3d"
               text-color="#b9c8e6" active-text-color="#ffffff">
        <el-menu-item index="/dashboard"><el-icon><DataAnalysis /></el-icon><span>总览 / 验收剧本</span></el-menu-item>
        <el-menu-item index="/sources"><el-icon><Notebook /></el-icon><span>来源账</span></el-menu-item>
        <el-menu-item index="/identity"><el-icon><Avatar /></el-icon><span>身份解析</span></el-menu-item>
        <el-menu-item index="/explorer"><el-icon><Share /></el-icon><span>受限图扩展</span></el-menu-item>
        <el-menu-item index="/patterns"><el-icon><MagicStick /></el-icon><span>模式与排序</span></el-menu-item>
        <el-menu-item index="/hitrate"><el-icon><Aim /></el-icon><span>命中率评估</span></el-menu-item>
        <el-menu-item index="/counter"><el-icon><Search /></el-icon><span>反证联查</span></el-menu-item>
        <el-menu-item index="/cases"><el-icon><FolderOpened /></el-icon><span>案件投影</span></el-menu-item>
        <el-menu-item index="/snapshots"><el-icon><Camera /></el-icon><span>快照复演</span></el-menu-item>
      </el-menu>
      <div class="aside-foot">
        <div>虚构网络演示数据</div>
        <div>截止日 2026-03-31</div>
      </div>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="hdr-title">{{ $route.meta.title || '平台' }}</div>
        <div class="flex gap8">
          <el-tag size="small" type="warning" effect="plain">评分≠违法结论</el-tag>
          <el-dropdown @command="onCmd">
            <span class="user-chip">
              <el-icon><UserFilled /></el-icon>
              {{ store.user?.display_name || store.user?.username }}
              <el-tag size="small" :type="store.isAdmin ? 'danger' : 'info'">
                {{ store.isAdmin ? '管理员' : '稽核员' }}
              </el-tag>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { store } from './store.js'
import { ElMessageBox } from 'element-plus'

const router = useRouter()
function onCmd(c) {
  if (c === 'logout') {
    ElMessageBox.confirm('确定退出登录？', '提示', { type: 'warning' }).then(() => {
      store.logout()
      router.push('/login')
    }).catch(() => {})
  }
}
</script>

<style scoped>
.aside { background: #0e1f3d; display: flex; flex-direction: column; }
.logo { display: flex; gap: 10px; align-items: center; color: #fff; padding: 18px 16px 14px; }
.logo-title { font-weight: 800; font-size: 15.5px; letter-spacing: .5px; }
.logo-sub { font-size: 11px; color: #7f96c4; margin-top: 2px; }
.menu { border-right: none; flex: 1; }
.menu .el-menu-item.is-active { background: #1d4ed8 !important; }
.aside-foot { color: #6f86b3; font-size: 11.5px; line-height: 1.8; padding: 14px 18px; }
.header { background: #fff; border-bottom: 1px solid var(--rg-line); display: flex;
  align-items: center; justify-content: space-between; height: 58px; }
.hdr-title { font-size: 17px; font-weight: 700; }
.user-chip { display: inline-flex; align-items: center; gap: 7px; cursor: pointer; color: #334155; outline: none; }
.main { background: var(--rg-bg); padding: 0; overflow-y: auto; }
.main :deep(.page) { min-height: 100%; }
</style>
