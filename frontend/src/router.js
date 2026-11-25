import { createRouter, createWebHashHistory } from 'vue-router'
import { store } from './store.js'

const routes = [
  { path: '/login', component: () => import('./pages/Login.vue'), meta: { public: true } },
  {
    path: '/', component: () => import('./AppLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('./pages/Dashboard.vue'), meta: { title: '总览与验收剧本' } },
      { path: 'sources', component: () => import('./pages/Sources.vue'), meta: { title: '来源账' } },
      { path: 'identity', component: () => import('./pages/Identity.vue'), meta: { title: '身份解析' } },
      { path: 'explorer', component: () => import('./pages/Explorer.vue'), meta: { title: '受限图扩展' } },
      { path: 'patterns', component: () => import('./pages/Patterns.vue'), meta: { title: '模式与排序' } },
      { path: 'hitrate', component: () => import('./pages/HitRate.vue'), meta: { title: '冻结排序命中率评估' } },
      { path: 'counter', component: () => import('./pages/Counter.vue'), meta: { title: '反证联查' } },
      { path: 'cases', component: () => import('./pages/Cases.vue'), meta: { title: '案件投影' } },
      { path: 'cases/:code', component: () => import('./pages/CaseDetail.vue'), meta: { title: '案件详情' } },
      { path: 'snapshots', component: () => import('./pages/Snapshots.vue'), meta: { title: '快照复演' } }
    ]
  }
]

const router = createRouter({ history: createWebHashHistory(), routes })
router.beforeEach((to) => {
  if (!to.meta.public && !store.token) return '/login'
})
export default router
