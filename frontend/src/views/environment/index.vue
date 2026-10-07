<template>
  <section class="page" data-module="environment">
    <header class="page-head">
      <div>
        <h2>环境监测站管理</h2>
        <p class="page-desc">辐照度、风速、风向以采集时刻的报文为准；缺测时段给空态说明，不沿用上一时刻读数。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记环境监测站</button>
        <button class="btn" type="button" @click="exportRows">导出环境监测站清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <section class="gap-panel">
      <header class="gap-head">
        <h3>缺测时段（{{ gaps.length }}）</h3>
        <button class="link" type="button" @click="loadGaps">刷新缺测时段</button>
      </header>
      <p v-if="!gaps.length" class="empty-state">当前没有缺测时段，各站点采集连续。</p>
      <ul v-else class="gap-list">
        <li v-for="gap in gaps" :key="gap.id" :class="{ closed: gap.已补齐 }">
          <strong>{{ gap.站点编号 }}</strong>
          <span>{{ gap.开始时间 }} ~ {{ gap.结束时间 }}</span>
          <em>{{ gap.已补齐 ? '已按采集顺序补传回填' : '待补传' }}</em>
          <span class="gap-hint">{{ gap.空态说明 }}</span>
          <span v-if="gap.巡视待办编号" class="gap-todo">巡视待办：{{ gap.巡视待办编号 }}</span>
        </li>
      </ul>
    </section>

    <section class="ingest-panel">
      <h3>采集 / 补传报文</h3>
      <p class="page-desc">按采集顺序粘贴 JSON 数组；站点编号、安装位置缺失或采集异常的单条会被退回并说明原因，可改正后重试；同一时段重复导入只留第一条。</p>
      <textarea v-model="ingestText" rows="6" class="ingest-input" :placeholder="ingestExample"></textarea>
      <div class="ingest-actions">
        <button class="btn primary" type="button" :disabled="ingesting" @click="submitIngest(false)">
          {{ ingesting ? '正在入库…' : '导入采集' }}
        </button>
        <button class="btn" type="button" :disabled="ingesting" @click="submitIngest(true)">失败条目重试</button>
        <span v-if="ingestMessage" class="ingest-message" :class="{ 'error-text': ingestHasFailed }">{{ ingestMessage }}</span>
      </div>
      <ul v-if="ingestResult?.failed?.length" class="ingest-failed">
        <li v-for="(item, idx) in ingestResult.failed" :key="idx">
          <em>{{ item.站点编号 || '（无站点编号）' }} {{ item.采集时间 || '（无采集时间）' }}</em>
          <span>{{ item.原因 }}</span>
        </li>
      </ul>
    </section>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>最近采集时间</th>
          <th>可执行动作</th>
          <th>采集时序</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column" :class="{ 'cell-missing': isMissing(row[column]) }">
            <template v-if="isMissing(row[column])">
              缺测
              <span v-if="(row.缺测字段 || []).includes(column)" class="missing-hint">{{ missingHint }}</span>
            </template>
            <template v-else>{{ row[column] }}</template>
          </td>
          <td>{{ row.最近采集时间 ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
          <td>
            <button class="link" type="button" @click="toggleDetail(row.id)">
              {{ openDetail === row.id ? '收起' : '查看' }}
            </button>
          </td>
        </tr>
        <tr v-if="openDetail">
          <td :colspan="columns.length + 3" class="detail-cell">
            <table class="timeline-table" v-if="detail && detail.采集时序?.length">
              <thead>
                <tr>
                  <th>采集时间</th>
                  <th v-for="f in metricFields" :key="f">{{ f }}</th>
                  <th>通讯状态</th>
                  <th>来源</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="point in detail.采集时序" :key="point.采集时间">
                  <td>{{ point.采集时间 }}</td>
                  <td v-for="f in metricFields" :key="f" :class="{ 'cell-missing': point[f] === null || point[f] === '' }">
                    {{ point[f] ?? '缺测' }}
                  </td>
                  <td>{{ point.通讯状态 }}</td>
                  <td>{{ point.补传 ? '补传回填' : '实时采集' }}</td>
                  <td class="gap-hint">{{ point.空态说明 || '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty-state">该站点还没有任何采集记录。</p>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无环境监测站数据，可先登记环境监测站</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条环境监测站记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = {
  id: number
  缺测字段?: string[]
  [field: string]: string | number | null | string[] | undefined
}
type Gap = {
  id: number
  站点编号: string
  开始时间: string
  结束时间: string
  已补齐: boolean
  空态说明: string
  巡视待办编号?: string
}
interface ReadingPoint {
  采集时间: string
  补传: boolean
  通讯状态: string
  空态说明: string
  缺测字段?: string[]
  [field: string]: string | boolean | null | string[] | undefined
}
interface Detail {
  id: number
  采集时序?: ReadingPoint[]
  [field: string]: unknown
}
interface IngestResult {
  message: string
  saved: ReadingPoint[]
  duplicated: Array<{ 站点编号: string; 采集时间: string }>
  backfilled: ReadingPoint[]
  failed: Array<{ 站点编号: string; 采集时间: string; 原因: string }>
  patrol_todos: Array<{ 记录编号: string; status: string }>
}

const ENDPOINT = '/api/environment'
const columns = ["站点编号", "安装位置", "辐照度", "环境温度", "风速", "风向", "积灰比", "通讯状态"]
const metricFields = ["辐照度", "环境温度", "风速", "风向", "积灰比"]
const actions = ["恢复正常", "标记异常", "停用站点"]
const stats = [{"label": "当日辐照总量", "value": 0}, {"label": "环境平均温度", "value": 0}, {"label": "故障站点数", "value": 0}]
const missingHint = '该时刻缺测，未沿用上一时刻读数'
const ingestExample = `[
  {
    "站点编号": "ENVI-0002",
    "安装位置": "2号方阵汇流箱旁",
    "采集时间": "2026-10-07 09:00",
    "辐照度": "520", "环境温度": "18.3",
    "风速": "2.5", "风向": "东", "积灰比": "4.7%",
    "补传": true
  }
]`

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const gaps = ref<Gap[]>([])
const openDetail = ref<number | null>(null)
const detail = ref<Detail | null>(null)

const ingestText = ref('')
const ingesting = ref(false)
const ingestMessage = ref('')
const ingestHasFailed = ref(false)
const ingestResult = ref<IngestResult | null>(null)

function isMissing(value: unknown): boolean {
  return value === null || value === undefined || value === ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '环境监测站登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('环境监测站动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站操作失败'
  }
}

async function toggleDetail(id: number) {
  if (openDetail.value === id) {
    openDetail.value = null
    detail.value = null
    return
  }
  openDetail.value = id
  detail.value = null
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) {
      throw new Error('环境监测站明细读取失败')
    }
    detail.value = (await response.json()) as Detail
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站明细读取失败'
  }
}

async function loadGaps() {
  try {
    const response = await request(`${ENDPOINT}/gaps`)
    if (!response.ok) {
      throw new Error('缺测时段读取失败')
    }
    const payload = (await response.json()) as { items: Gap[] }
    gaps.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺测时段读取失败'
  }
}

async function submitIngest(isRetry: boolean) {
  errorMessage.value = ''
  ingestMessage.value = ''
  ingestHasFailed.value = false
  let records: unknown
  try {
    records = JSON.parse(ingestText.value || '[]')
  } catch {
    ingestHasFailed.value = true
    ingestMessage.value = '报文不是合法 JSON，请检查后重试'
    return
  }
  if (!Array.isArray(records) || records.length === 0) {
    ingestHasFailed.value = true
    ingestMessage.value = '报文需为非空 JSON 数组'
    return
  }
  ingesting.value = true
  try {
    const response = await request(`${ENDPOINT}/readings/ingest`, {
      method: 'POST',
      body: JSON.stringify({ records, retry: isRetry }),
    })
    const payload = (await response.json()) as IngestResult & { detail?: string }
    if (!response.ok) {
      throw new Error(payload.detail || '采集入库失败，请重试')
    }
    ingestResult.value = payload
    ingestMessage.value = payload.message
    ingestHasFailed.value = payload.failed.length > 0
    await Promise.all([reload(), loadGaps()])
  } catch (error) {
    ingestHasFailed.value = true
    ingestMessage.value = error instanceof Error ? error.message : '采集入库失败，请重试'
  } finally {
    ingesting.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('环境监测站列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (openDetail.value) {
      await toggleDetail(openDetail.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadGaps()
})
</script>

<style scoped>
.gap-panel,
.ingest-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
}

.gap-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.gap-head h3,
.ingest-panel h3 {
  margin: 0 0 8px;
}

.gap-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: grid;
  gap: 6px;
}

.gap-list li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  padding: 6px 8px;
  border: 1px dashed var(--border);
  border-radius: 6px;
}

.gap-list li.closed {
  opacity: 0.65;
}

.gap-list em {
  font-style: normal;
  color: #b42318;
}

.gap-list li.closed em {
  color: #067647;
}

.gap-hint {
  color: var(--muted);
  flex-basis: 100%;
}

.gap-todo {
  color: #b54708;
}

.ingest-input {
  width: 100%;
  font: inherit;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  resize: vertical;
}

.ingest-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-top: 8px;
}

.ingest-message {
  color: #067647;
}

.ingest-failed {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #b42318;
}

.ingest-failed span {
  color: var(--muted);
  margin-left: 8px;
}

.cell-missing {
  color: #b42318;
}

.missing-hint {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.detail-cell {
  padding: 10px 12px;
  background: #fafafa;
}

.timeline-table {
  width: 100%;
}
</style>
