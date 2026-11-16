<template>
  <section class="page" data-module="cleanse">
    <header class="page-head">
      <div>
        <h2>数据清洗管理</h2>
        <p class="page-desc">
          每个清洗批次固定启动时的规则版本与快照，规则停用只阻止新批次；历史批次保留原版本与清洗值，
          重复切换、重复提交不改写已有记录。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记清洗规则</button>
        <button class="btn" type="button" @click="exportRows">导出数据清洗清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="label in statLabels" :key="label" class="stat-card">
        <span class="stat-label">{{ label }}</span>
        <strong class="stat-value">{{ stats[label] }}</strong>
      </article>
    </div>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <footer v-if="message" class="page-foot">
      <span :class="messageOk ? 'success-text' : 'error-text'">{{ message }}</span>
    </footer>

    <!-- 规则列表 -->
    <div v-if="activeTab === 'rules'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in ruleColumns" :key="column">{{ column }}</th>
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rules" :key="String(row.id)">
            <td v-for="column in ruleColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td><span class="badge" :class="statusClass(row.status)">{{ row.status }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" @click="runAction('提交校验', row)">提交校验</button>
              <button class="link" type="button" @click="runAction('启用规则', row)">启用规则</button>
              <button class="link" type="button" @click="runAction('停用规则', row)">停用规则</button>
              <button class="link" type="button" @click="openEdit(row)">编辑保存</button>
            </td>
          </tr>
          <tr v-if="!rules.length">
            <td :colspan="ruleColumns.length + 2" class="empty-state">暂无清洗规则，可先登记清洗规则</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 清洗批次 -->
    <div v-else-if="activeTab === 'batches'">
      <p class="muted">批次列表按批次自身固定的规则版本回显，不跟随规则当前版本变化；点击「批次详情」核对历史清洗值。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次编号</th><th>规则编号</th><th>固定规则版本</th><th>批次状态</th>
            <th>审计状态</th><th>输入/输出行数</th><th>启动时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in batches" :key="String(batch.id)">
            <td>{{ batch.批次编号 }}</td>
            <td>{{ batch.规则编号 ?? ruleName(batch.规则ID) }}</td>
            <td><strong>v{{ batch.规则版本 }}</strong></td>
            <td><span class="badge" :class="batchClass(batch.状态)">{{ batch.状态 }}</span></td>
            <td>
              <span v-if="batch.审计状态" class="badge pending">{{ batch.审计状态 }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td>{{ batch.指标?.输入行数 ?? 0 }} / {{ batch.指标?.输出行数 ?? 0 }}</td>
            <td>{{ batch.启动时间 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openBatch(batch.id)">批次详情</button>
              <button
                v-if="batch.状态 !== '已完成'"
                class="link"
                type="button"
                @click="batchAction('interrupt', batch.id)"
              >中断</button>
              <button
                v-if="batch.状态 === '已中断'"
                class="link"
                type="button"
                @click="batchAction('resume', batch.id)"
              >续做</button>
              <button
                v-if="batch.状态 !== '已完成'"
                class="link"
                type="button"
                @click="batchAction('submit', batch.id)"
              >提交结果</button>
              <button
                v-if="batch.状态 === '已完成'"
                class="link"
                type="button"
                @click="batchAction('submit', batch.id)"
              >重复提交</button>
            </td>
          </tr>
          <tr v-if="!batches.length">
            <td colspan="8" class="empty-state">暂无清洗批次，请在规则处于「已启用」时启动新批次</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 清洗数据集 -->
    <div v-else-if="activeTab === 'dataset'">
      <div class="page-head">
        <div>
          <h3 class="section-title">清洗数据集（更新节点）</h3>
          <p class="muted">
            取数入口 {{ datasetNode.取数入口 ?? '—' }} ｜ 更新节点 {{ datasetNode.更新节点 ?? '—' }}
          </p>
        </div>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="startBatchFromDataset">按启用规则启动新批次</button>
          <button class="btn" type="button" @click="restoreDataset">恢复清洗数据集</button>
        </div>
      </div>
      <table class="data-table">
        <thead>
          <tr><th v-for="column in datasetColumns" :key="column">{{ column }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in datasetRows" :key="String(row.id)">
            <td v-for="column in datasetColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!datasetRows.length">
            <td :colspan="datasetColumns.length" class="empty-state">清洗数据集为空，可恢复到初始基线</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 清洗审计待处理区 -->
    <div v-else-if="activeTab === 'audit'">
      <p class="muted">停用清洗规则时仍在处理中的批次会落到这里暂存，批次固定原规则版本；处理优先级待定，每个批次只固定一个规则版本。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次编号</th><th>规则编号</th><th>固定规则版本</th><th>批次状态</th>
            <th>审计状态</th><th>处理优先级</th><th>冲突原因</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in auditRows" :key="String(item.id)">
            <td>{{ item.批次编号 }}</td>
            <td>{{ item.规则编号 }}</td>
            <td><strong>v{{ item.规则版本 }}</strong></td>
            <td>{{ item.批次状态 }}</td>
            <td><span class="badge pending">{{ item.审计状态 }}</span></td>
            <td>{{ item.处理优先级 }}</td>
            <td>{{ item.冲突原因 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openBatch(item.批次ID)">批次详情</button>
            </td>
          </tr>
          <tr v-if="!auditRows.length">
            <td colspan="8" class="empty-state">审计待处理区暂无记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 批次详情抽屉：同步回显固定规则版本与历史清洗值 -->
    <div v-if="drawerBatch" class="drawer-mask" @click.self="drawerBatch = null">
      <aside class="drawer">
        <div class="drawer-head">
          <h3>批次详情 · {{ drawerBatch.批次编号 }}</h3>
          <button class="btn ghost" type="button" @click="drawerBatch = null">关闭</button>
        </div>
        <dl class="kv-grid">
          <dt>固定规则版本</dt><dd><strong>v{{ drawerBatch.规则版本 }}</strong></dd>
          <dt>批次状态</dt><dd>{{ drawerBatch.状态 }}</dd>
          <dt>审计状态</dt><dd>{{ drawerBatch.审计状态 || '—' }}</dd>
          <dt>启动时间</dt><dd>{{ drawerBatch.启动时间 }}</dd>
          <dt>更新时间</dt><dd>{{ drawerBatch.更新时间 }}</dd>
        </dl>
        <h4 class="section-title">启动时固定的规则快照</h4>
        <dl class="kv-grid">
          <template v-for="field in snapshotFields" :key="field">
            <dt>{{ field }}</dt><dd>{{ drawerBatch.规则快照?.[field] ?? '—' }}</dd>
          </template>
        </dl>
        <h4 class="section-title">清洗指标</h4>
        <div class="metric-grid">
          <div v-for="(value, key) in drawerBatch.指标" :key="key" class="metric-cell">
            <span>{{ key }}</span><strong>{{ value }}</strong>
          </div>
        </div>
        <h4 class="section-title">历史清洗结果（按固定版本，不被后续切换覆盖）</h4>
        <table class="record-table">
          <thead>
            <tr><th v-for="column in resultColumns(drawerBatch)" :key="column">{{ column }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(record, index) in drawerBatch.清洗结果" :key="index">
              <td v-for="column in resultColumns(drawerBatch)" :key="column">{{ record[column] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </aside>
    </div>

    <!-- 登记 / 编辑规则弹窗：全字段保存，避免去重键、格式校验、空值策略、生效版本丢失 -->
    <div v-if="editing" class="modal-mask" @click.self="editing = null">
      <div class="modal">
        <h3>{{ editing.id ? '编辑清洗规则（保存生成新版本）' : '登记清洗规则' }}</h3>
        <div class="form-grid">
          <label v-for="field in editableFields" :key="field">
            {{ field }}<input v-model="editing[field]" :placeholder="`请输入${field}`" />
          </label>
        </div>
        <p class="muted" style="margin-top: 8px">
          编辑保存只对之后启动的新批次生效；已存在批次继续固定原规则版本，历史清洗值不变。
        </p>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="editing = null">取消</button>
          <button class="btn primary" type="button" @click="saveRule">保存</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Batch = {
  id: number
  批次编号: string
  规则ID: number
  规则编号?: string
  规则版本: string
  状态: string
  审计状态?: string
  启动时间: string
  更新时间: string
  指标?: Record<string, number>
  规则快照?: Record<string, string>
  清洗结果?: Row[]
  原始记录?: Row[]
}
type AuditItem = {
  id: number
  批次ID: number
  批次编号: string
  规则编号: string
  规则版本: string
  批次状态: string
  审计状态: string
  处理优先级: string
  冲突原因: string
}
type DatasetNode = {
  取数入口?: string
  更新节点?: string
  total?: number
  items?: Row[]
}

const ENDPOINT = '/api/cleanse'
const ruleColumns = ['规则编号', '目标字段', '空值替换', '格式规范化', '异常值剔除', '去重键', '规则版本', '生效日期']
const editableFields = ['规则编号', '目标字段', '空值替换', '格式规范化', '异常值剔除', '去重键', '规则版本', '生效日期']
const snapshotFields = ['规则编号', '目标字段', '空值替换', '格式规范化', '异常值剔除', '去重键', '规则版本', '生效日期']
const tabs = [
  { key: 'rules', label: '清洗规则' },
  { key: 'batches', label: '清洗批次' },
  { key: 'dataset', label: '清洗数据集' },
  { key: 'audit', label: '清洗审计待处理区' },
] as const

type TabKey = (typeof tabs)[number]['key']
const activeTab = ref<TabKey>('rules')

const rules = ref<Row[]>([])
const batches = ref<Batch[]>([])
const auditRows = ref<AuditItem[]>([])
const datasetRows = ref<Row[]>([])
const datasetNode = ref<DatasetNode>({})
const drawerBatch = ref<Batch | null>(null)
const editing = ref<Record<string, string> | null>(null)
const message = ref('')
const messageOk = ref(true)
const statLabels = ['清洗规则总数', '已启用规则', '清洗批次数', '审计待处理'] as const
type StatKey = (typeof statLabels)[number]
const stats = ref<Record<StatKey, number>>({
  清洗规则总数: 0,
  已启用规则: 0,
  清洗批次数: 0,
  审计待处理: 0,
})

function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

function statusClass(status: string | number | null) {
  const text = String(status ?? '')
  if (text === '已启用') return 'run'
  if (text === '已停用') return 'off'
  return 'pending'
}

function batchClass(status: string) {
  if (status === '已完成') return 'done'
  if (status === '运行中') return 'run'
  return 'pending'
}

function ruleName(ruleId: number) {
  return rules.value.find((row) => Number(row.id) === ruleId)?.规则编号 ?? `规则#${ruleId}`
}

function datasetColumns() {
  // 取数据集第一条记录的键作为列，避免写死字段导致取数口径变化后缺列
  const first = datasetRows.value[0]
  return first ? Object.keys(first).filter((key) => key !== 'id') : ['用户ID', '手机号', '城市']
}

function resultColumns(batch: Batch) {
  const first = batch.清洗结果?.[0]
  return first ? Object.keys(first).filter((key) => key !== 'id') : []
}

async function loadRules() {
  const response = await request(`${ENDPOINT}?size=200`)
  if (!response.ok) throw new Error('清洗规则列表读取失败')
  const payload = await response.json()
  rules.value = payload.items ?? []
  stats.value.清洗规则总数 = rules.value.length
  stats.value.已启用规则 = rules.value.filter((row) => row.status === '已启用').length
}

async function loadBatches() {
  const response = await request(`${ENDPOINT}/batches`)
  if (!response.ok) throw new Error('清洗批次列表读取失败')
  const payload = await response.json()
  batches.value = payload.items ?? []
  stats.value.清洗批次数 = batches.value.length
}

async function loadAudit() {
  const response = await request(`${ENDPOINT}/audit`)
  if (!response.ok) throw new Error('清洗审计待处理区读取失败')
  const payload = await response.json()
  auditRows.value = payload.items ?? []
  stats.value.审计待处理 = auditRows.value.filter((item) => item.审计状态 === '待处理').length
}

async function loadDataset() {
  const response = await request(`${ENDPOINT}/dataset`)
  if (!response.ok) throw new Error('清洗数据集读取失败')
  const node: DatasetNode = await response.json()
  datasetNode.value = node
  datasetRows.value = node.items ?? []
}

async function refreshAll() {
  await Promise.all([loadRules(), loadBatches(), loadAudit()])
  if (activeTab.value === 'dataset') await loadDataset()
}

async function switchTab(key: TabKey) {
  activeTab.value = key
  message.value = ''
  try {
    if (key === 'rules') await loadRules()
    else if (key === 'batches') await loadBatches()
    else if (key === 'audit') await loadAudit()
    else await loadDataset()
  } catch (error) {
    notify(error instanceof Error ? error.message : '数据加载失败', false)
  }
}

async function runAction(action: string, row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message ?? '数据清洗动作未生效')
    notify(payload.message, true)
    await refreshAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '数据清洗操作失败', false)
  }
}

function openCreate() {
  editing.value = { 规则编号: '', 目标字段: '', 空值替换: '', 格式规范化: '', 异常值剔除: '', 去重键: '', 规则版本: '', 生效日期: '' }
}

function openEdit(row: Row) {
  const form: Record<string, string> = { id: String(row.id) }
  for (const field of editableFields) form[field] = String(row[field] ?? '')
  editing.value = form
}

async function saveRule() {
  if (!editing.value) return
  const values = { ...editing.value }
  const id = values.id
  delete values.id
  try {
    const url = id ? `${ENDPOINT}/${id}` : ENDPOINT
    const method = id ? 'PUT' : 'POST'
    const response = await request(url, { method, body: JSON.stringify({ values }) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message ?? '规则保存失败')
    notify(payload.message, true)
    editing.value = null
    await refreshAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '规则保存失败', false)
  }
}

async function startBatchFromDataset() {
  const enabled = rules.value.filter((row) => row.status === '已启用')
  if (!enabled.length) {
    notify('当前没有「已启用」的清洗规则，无法启动新批次', false)
    return
  }
  try {
    const response = await request(`${ENDPOINT}/batches`, {
      method: 'POST',
      body: JSON.stringify({ values: { rule_id: enabled[0].id } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message ?? '批次启动失败')
    notify(payload.message, true)
    activeTab.value = 'batches'
    await refreshAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '批次启动失败', false)
  }
}

async function restoreDataset() {
  try {
    const response = await request(`${ENDPOINT}/dataset/restore`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message ?? '数据集恢复失败')
    notify(payload.message, true)
    await loadDataset()
    await loadBatches()
  } catch (error) {
    notify(error instanceof Error ? error.message : '数据集恢复失败', false)
  }
}

async function batchAction(kind: 'submit' | 'interrupt' | 'resume', batchId: number) {
  try {
    const response = await request(`${ENDPOINT}/batches/${batchId}/${kind}`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message ?? '批次操作失败')
    notify(payload.message, true)
    await refreshAll()
    if (drawerBatch.value && drawerBatch.value.id === batchId) await openBatch(batchId)
  } catch (error) {
    notify(error instanceof Error ? error.message : '批次操作失败', false)
  }
}

async function openBatch(batchId: number) {
  try {
    const response = await request(`${ENDPOINT}/batches/${batchId}`)
    if (!response.ok) throw new Error('批次详情读取失败')
    drawerBatch.value = (await response.json()) as Batch
  } catch (error) {
    notify(error instanceof Error ? error.message : '批次详情读取失败', false)
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(refreshAll)
</script>
