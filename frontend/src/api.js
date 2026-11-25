import axios from 'axios'
import { ElMessage } from 'element-plus'
import { store } from './store.js'
import router from './router.js'

const api = axios.create({ baseURL: '/', timeout: 30000 })

api.interceptors.request.use((cfg) => {
  if (store.token) cfg.headers.Authorization = `Bearer ${store.token}`
  return cfg
})

api.interceptors.response.use(
  (r) => r.data,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.message || '请求失败'
    if (status === 401) {
      store.token = ''
      store.user = null
      localStorage.removeItem('rg_token')
      if (router.currentRoute.value.path !== '/login') router.push('/login')
    }
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    return Promise.reject(err)
  }
)

export default api
