<template>
  <section class="page" data-module="environment">
    <header class="page-head">
      <div>
        <h2>环境监测站管理</h2>
        <p class="page-desc">缺测时段只显示空态、可重试补录，不会拿上一时刻的读数顶替；列表与详情同一段取数。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = !showCreate">登记环境监测站</button>
        <button class="btn" type="button" @click="exportRows">导出环境监测站清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="showCreate" class="filter-bar" @submit.prevent="submitCreate">
      <label class="filter-item">
        <span>站点编号</span>
        <input v-model="createForm.站点编号" placeholder="如 ENVI-0004" />
      </label>
      <label class="filter-item">
        <span>安装位置</span>
        <input v-model="createForm.安装位置" placeholder="如 三号方阵北侧气象杆" />
      </label>
      <button class="btn primary" type="submit">保存</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>站点编号</span>
        <input v-model="filters.keyword" placeholder="按站点编号检索" />
      </label>
      <label class="filter-item">
        <span>数据状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-selected': detail?.id === row.id }">
          <td v-for="column in columns" :key="column">
            <span v-if="isMeasureColumn(column) && row[column] == null" class="missing-tag">缺测</span>
            <span v-else-if="column === '缺测时段'">{{ gapSummary(row) }}</span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
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
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无环境监测站数据，可先登记环境监测站</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel">
      <header class="detail-head">
        <h3>{{ detail.站点编号 }} · {{ detail.安装位置 }}</h3>
        <button class="btn ghost" type="button" @click="closeDetail">收起</button>
      </header>
      <p class="page-desc">
        当前时段 {{ detail.当前时段 ?? '暂无采集' }}；积灰比
        <strong v-if="detail.积灰比 != null">{{ detail.积灰比 }}</strong>
        <span v-else class="missing-tag">缺测</span>
        （与列表同一段取数，两边一致）；通讯状态 {{ detail.通讯状态 ?? '未知' }}
      </p>

      <div v-if="detail.缺测时段?.length" class="gap-list">
        <p v-for="gap in detail.缺测时段" :key="gap.开始" class="gap-item">
          <span class="missing-tag">缺测</span>
          {{ gap.说明 }}
        </p>
      </div>
      <p v-else class="page-desc">当前没有缺测时段。</p>

      <table class="data-table">
        <thead>
          <tr>
            <th>采集时间</th>
            <th v-for="field in measureFields" :key="field">{{ field }}</th>
            <th>通讯状态</th>
            <th>记录说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in detail.采集记录" :key="record.采集时间">
            <td>{{ record.采集时间 }}</td>
            <td v-for="field in measureFields" :key="field">
              <span v-if="record[field] == null" class="missing-tag">缺测</span>
              <span v-else>{{ record[field] }}</span>
            </td>
            <td>{{ record.通讯状态 ?? '—' }}</td>
            <td>
              <span v-if="record.缺测" class="missing-tag">空测·待补录</span>
              <span v-else-if="record.补录" class="backfill-tag">补录</span>
              <span v-else>正常</span>
            </td>
          </tr>
          <tr v-if="!detail.采集记录?.length">
            <td :colspan="measureFields.length + 3" class="empty-state">
              暂无采集记录：该站点还没有任何时段的数据，导入后此处按采集时间排列
            </td>
          </tr>
        </tbody>
      </table>

      <form class="import-panel" @submit.prevent="submitReadings">
        <label class="filter-item import-input">
          <span>采集数据（每行一条：采集时间,辐照度,风速,风向,积灰比；留空字段记为缺测，不拿上一时刻顶替）</span>
          <textarea
            v-model="importText"
            rows="4"
            placeholder="2026-10-07 11:00,901.2,4.5,西南,0.94"
          ></textarea>
        </label>
        <div class="page-actions">
          <button class="btn primary" type="submit">导入采集数据</button>
          <button v-if="missingMoments.length" class="btn" type="button" @click="retryMissing">
            重试缺测时段（{{ missingMoments.length }} 个，按采集顺序接着补）
          </button>
        </div>
      </form>
      <p v-if="ingestMessage" class="page-desc">{{ ingestMessage }}</p>
      <ul v-if="rejected.length" class="reject-list">
        <li v-for="item in rejected" :key="item.序号">
          第 {{ item.序号 }} 条（{{ item.采集时间 ?? '无采集时间' }}）：{{ item.原因 }}
        </li>
      </ul>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条环境监测站记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/environment'
const measureFields = ['辐照度', '风速', '风向', '积灰比', '环境温度']
const columns = ['站点编号', '安装位置', '当前时段', '辐照度', '风速', '风向', '积灰比', '通讯状态', '缺测时段']
const actions = ['重试采集', '恢复正常', '标记异常', '停用站点']
const statuses = ['数据正常', '数据异常', '传感器故障', '已停用']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref({ keyword: '', status: '' })
const showCreate = ref(false)
const createForm = ref({ 站点编号: '', 安装位置: '' })
const detail = ref<Row | null>(null)
const importText = ref('')
const ingestMessage = ref('')
const rejected = ref<Row[]>([])

const stats = computed(() => [
  { label: '站点总数', value: total.value },
  { label: '缺测采集点', value: rows.value.reduce((sum, row) => sum + Number(row.缺测点数 ?? 0), 0) },
  { label: '通讯中断站点', value: rows.value.filter((row) => row.通讯状态 === '中断').length },
])

// 缺测时段展开成逐个采集点，按采集顺序排列，供重试时接着补
const missingMoments = computed<string[]>(() => {
  const gaps = detail.value?.缺测时段 ?? []
  return gaps.flatMap((gap: Row) => (Array.isArray(gap.时点) ? gap.时点 : []))
})

function isMeasureColumn(column: string) {
  return measureFields.includes(column)
}

function gapSummary(row: Row) {
  const gaps = row.缺测时段 ?? []
  return gaps.length ? `${gaps.length} 段待补（${gaps[0].开始} 起）` : '无'
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function parseImportLines(text: string): Row[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [moment, irradiance, windSpeed, windDir, dustRatio] = line.split(/[,，]/).map((cell) => cell.trim())
      const record: Row = { 采集时间: moment }
      if (irradiance) record.辐照度 = irradiance
      if (windSpeed) record.风速 = windSpeed
      if (windDir) record.风向 = windDir
      if (dustRatio) record.积灰比 = dustRatio
      return record
    })
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '环境监测站登记失败')
    }
    showCreate.value = false
    createForm.value = { 站点编号: '', 安装位置: '' }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '环境监测站动作未生效，请稍后重试')
    }
    await reload()
    if (action === '重试采集' && payload.entry) {
      detail.value = payload.entry
      ingestMessage.value = payload.message
      rejected.value = []
    } else if (detail.value?.id === row.id) {
      await openDetail(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站操作失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('环境监测站详情读取失败')
    }
    detail.value = await response.json()
    ingestMessage.value = ''
    rejected.value = []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站详情读取失败'
  }
}

function closeDetail() {
  detail.value = null
  ingestMessage.value = ''
  rejected.value = []
}

async function submitReadings() {
  await ingest(parseImportLines(importText.value))
}

// 采集异常后的重试：只补还没取到的时段，按采集时间顺序接着写，而不是整段重发
async function retryMissing() {
  const wanted = new Set(missingMoments.value)
  const readings = parseImportLines(importText.value)
    .filter((record) => wanted.has(String(record.采集时间).replace('T', ' ')))
    .sort((a, b) => String(a.采集时间).localeCompare(String(b.采集时间)))
  if (!readings.length) {
    ingestMessage.value = `请在导入框中补充这些时段的数据：${[...wanted].join('、')}`
    return
  }
  await ingest(readings)
}

async function ingest(readings: Row[]) {
  if (!detail.value) return
  errorMessage.value = ''
  ingestMessage.value = ''
  rejected.value = []
  if (!readings.length) {
    ingestMessage.value = '没有可导入的采集记录'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}/readings`, {
      method: 'POST',
      body: JSON.stringify({ readings }),
    })
    const payload = await response.json()
    ingestMessage.value = payload.message ?? ''
    rejected.value = payload.rejected ?? []
    if (payload.ok && payload.entry) {
      detail.value = payload.entry
      await reload()
    }
  } catch (error) {
    ingestMessage.value = error instanceof Error ? error.message : '采集导入失败，可重试'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('环境监测站列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '环境监测站列表读取失败'
  }
}

onMounted(reload)
</script>
