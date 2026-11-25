import { reactive } from 'vue'

export const store = reactive({
  token: localStorage.getItem('rg_token') || '',
  user: JSON.parse(localStorage.getItem('rg_user') || 'null'),
  setAuth(token, user) {
    this.token = token
    this.user = user
    localStorage.setItem('rg_token', token)
    localStorage.setItem('rg_user', JSON.stringify(user))
  },
  logout() {
    this.token = ''
    this.user = null
    localStorage.removeItem('rg_token')
    localStorage.removeItem('rg_user')
  },
  get isAdmin() {
    return this.user?.role === 'admin'
  }
})

// 边类型 / 节点类型的中文标签与配色（图谱与各页面共用）
export const EDGE_META = {
  identity_strong: { label: '强身份关联', color: '#7c3aed' },
  identity_weak: { label: '弱身份候选·待核', color: '#d97706', dash: '5 4' },
  person_visit: { label: '就诊', color: '#64748b' },
  visit_org: { label: '就诊机构', color: '#94a3b8' },
  visit_doctor: { label: '接诊医师', color: '#94a3b8' },
  visit_rx: { label: '就诊—处方', color: '#0ea5e9' },
  rx_person: { label: '处方归属', color: '#94a3b8' },
  prescription_edge: { label: '处方', color: '#0284c7' },
  rx_org: { label: '开方机构', color: '#94a3b8' },
  rx_doctor: { label: '开方医师', color: '#94a3b8' },
  rx_settlement: { label: '处方—结算', color: '#0d9488' },
  settlement_edge: { label: '结算', color: '#0f766e' },
  settle_org: { label: '结算机构', color: '#64748b' },
  settle_supply: { label: '结算—供药', color: '#16a34a' },
  supply_org: { label: '供药药店', color: '#15803d' },
  supply_delivery: { label: '配送签收', color: '#65a30d' },
  ingredient_overlap: { label: '同成分重叠', color: '#dc2626', width: 2.6 },
  schedule_conflict: { label: '停诊冲突', color: '#ea580c', width: 2.4 },
  practitioner_schedule: { label: '排班', color: '#94a3b8' },
  schedule_org: { label: '排班机构', color: '#cbd5e1' },
  split_group: { label: '拆方组', color: '#dc2626', width: 2.4 },
  central_group: { label: '集中结算组', color: '#dc2626', width: 2.4 },
  person_referral: { label: '转诊', color: '#2563eb' },
  referral_from: { label: '转出机构', color: '#94a3b8' },
  referral_to: { label: '转入机构', color: '#1d4ed8' },
  referral_settlement: { label: '转诊后结算', color: '#0d9488' },
  person_bed: { label: '床位/住养（反证）', color: '#16a34a' },
  bed_org: { label: '床位机构', color: '#65a30d' },
  person_appeal: { label: '申诉', color: '#16a34a' },
  appeal_return_for: { label: '申诉确认退回', color: '#15803d', width: 2.4 },
  transfer_from: { label: '调拨出', color: '#94a3b8' },
  transfer_to: { label: '调拨入', color: '#16a34a' },
  explain_transfer: { label: '调拨解释（反证）', color: '#15803d', width: 2.4 }
}

export const NODE_META = {
  person: { label: '参保人', color: '#1d4ed8' },
  org: { label: '机构', color: '#0f766e' },
  practitioner: { label: '医师', color: '#7c3aed' },
  visit: { label: '就诊', color: '#0ea5e9' },
  rx: { label: '处方', color: '#0284c7' },
  settlement: { label: '结算/索赔', color: '#0d9488' },
  supply: { label: '供药', color: '#16a34a' },
  delivery: { label: '签收', color: '#65a30d' },
  schedule: { label: '排班', color: '#ea580c' },
  referral: { label: '转诊', color: '#2563eb' },
  appeal: { label: '申诉', color: '#15803d' },
  transfer: { label: '调拨', color: '#15803d' },
  bed: { label: '床位服务', color: '#65a30d' }
}

export function yuan(v) {
  return '¥' + Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export const STATUS_TYPE = {
  pending: 'warning', reviewing: 'primary', new: 'info',
  confirmed_risk_clue: 'danger', excluded_legitimate: 'success',
  closed_returned: 'success', closed_weak_identity: 'info', weak_identity: 'warning'
}
