<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import { DataLine, Download } from '@element-plus/icons-vue'

const props = defineProps<{ token: string; currentUserId: number }>()
const emit = defineEmits(['close'])

const activeTab = ref('users')
const users = ref<any[]>([])
const loginLogs = ref<any[]>([])
const aiLogs = ref<any[]>([])
const aiUserStats = ref<any[]>([])
const aiContentLogs = ref<any[]>([])
const loading = ref(false)
const exportLoading = ref(false)
const aiDetailLoading = ref(false)
const aiDetailVisible = ref(false)
const aiDetail = ref<any | null>(null)
const roleUpdatingId = ref<number | null>(null)

const loginPage = ref(1)
const loginPageSize = ref(20)
const loginTotal = ref(0)

const aiPage = ref(1)
const aiPageSize = ref(20)
const aiTotal = ref(0)

const aiContentPage = ref(1)
const aiContentPageSize = ref(20)
const aiContentTotal = ref(0)
const aiContentUserId = ref<number | null>(null)
const aiContentKeyword = ref('')

const api = axios.create({ baseURL: '/api' })
api.interceptors.request.use((config) => {
    config.headers.Authorization = `Bearer ${props.token}`
    return config
})

const getAiStatusType = (status?: string) => {
    return status === 'failed' ? 'danger' : 'success'
}

const fetchUsers = async () => {
    loading.value = true
    try {
        const res = await api.get('/admin/users')
        users.value = res.data
    } catch (e) {
        ElMessage.error('无法获取用户列表')
    } finally {
        loading.value = false
    }
}

const updateUserRole = async (target: any) => {
    const nextIsAdmin = !Boolean(target.is_admin)
    const action = nextIsAdmin ? '设为管理员' : '取消管理员权限'
    try {
        await ElMessageBox.confirm(
            `确定要将“${target.username}”${action}吗？`,
            '确认角色变更',
            { type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消' },
        )
        roleUpdatingId.value = target.id
        const response = await api.patch(`/admin/users/${target.id}/role`, {
            is_admin: nextIsAdmin,
        })
        Object.assign(target, response.data)
        ElMessage.success('用户角色已更新')
    } catch (error: any) {
        if (error !== 'cancel' && error !== 'close') {
            ElMessage.error(error?.response?.data?.detail || '更新用户角色失败')
        }
    } finally {
        roleUpdatingId.value = null
    }
}

const fetchLoginLogs = async () => {
    loading.value = true
    try {
        const res = await api.get(`/admin/logs/login?page=${loginPage.value}&page_size=${loginPageSize.value}`)
        if (res.data.items) {
            loginLogs.value = res.data.items
            loginTotal.value = res.data.total
        } else {
            loginLogs.value = res.data
            loginTotal.value = res.data.length
        }
    } catch (e) {
        ElMessage.error('无法获取登录日志')
    } finally {
        loading.value = false
    }
}

const fetchAiLogs = async () => {
    loading.value = true
    try {
        const res = await api.get(`/admin/logs/ai?page=${aiPage.value}&page_size=${aiPageSize.value}`)
        if (res.data.items) {
            aiLogs.value = res.data.items
            aiTotal.value = res.data.total
        } else {
            aiLogs.value = res.data
            aiTotal.value = res.data.length
        }
    } catch (e) {
        ElMessage.error('无法获取 AI 日志')
    } finally {
        loading.value = false
    }
}

const fetchAiUserStats = async () => {
    try {
        const res = await api.get('/admin/logs/ai/users')
        aiUserStats.value = Array.isArray(res.data?.items) ? res.data.items : []
    } catch (e) {
        console.error(e)
        aiUserStats.value = []
    }
}

const fetchAiContentLogs = async () => {
    loading.value = true
    try {
        const params = new URLSearchParams()
        params.set('page', String(aiContentPage.value))
        params.set('page_size', String(aiContentPageSize.value))
        if (aiContentUserId.value !== null && aiContentUserId.value !== undefined) {
            params.set('user_id', String(aiContentUserId.value))
        }
        const keyword = aiContentKeyword.value.trim()
        if (keyword) {
            params.set('keyword', keyword)
        }
        const res = await api.get(`/admin/logs/ai?${params.toString()}`)
        if (res.data.items) {
            aiContentLogs.value = res.data.items
            aiContentTotal.value = res.data.total
        } else {
            aiContentLogs.value = res.data
            aiContentTotal.value = res.data.length
        }
    } catch (e) {
        console.error(e)
        ElMessage.error('无法获取 AI 内容审计日志')
    } finally {
        loading.value = false
    }
}

const resetAiContentFilter = async () => {
    aiContentUserId.value = null
    aiContentKeyword.value = ''
    aiContentPage.value = 1
    await fetchAiContentLogs()
}

const applyAiContentFilter = async () => {
    aiContentPage.value = 1
    await fetchAiContentLogs()
}

const openAiDetail = async (logId: number) => {
    aiDetailVisible.value = true
    aiDetailLoading.value = true
    aiDetail.value = null
    try {
        const res = await api.get(`/admin/logs/ai/${logId}`)
        aiDetail.value = res.data || null
    } catch (e) {
        console.error(e)
        ElMessage.error('无法获取该条 AI 日志详情')
        aiDetailVisible.value = false
    } finally {
        aiDetailLoading.value = false
    }
}

const downloadAllUserData = async () => {
    exportLoading.value = true
    try {
        const response = await api.get('/admin/export/all', { responseType: 'blob' })
        const blobUrl = window.URL.createObjectURL(new Blob([response.data], { type: 'application/zip' }))
        const link = document.createElement('a')
        link.href = blobUrl

        const contentDisposition = response.headers['content-disposition'] || ''
        const fileNameMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
        const fileName = fileNameMatch?.[1]
            ? decodeURIComponent(fileNameMatch[1])
            : `luminascript_admin_export_${Date.now()}.zip`

        link.setAttribute('download', fileName)
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        window.URL.revokeObjectURL(blobUrl)
        ElMessage.success('已开始下载全部用户数据')
    } catch (e) {
        console.error(e)
        ElMessage.error('导出全部用户数据失败')
    } finally {
        exportLoading.value = false
    }
}

const handleTabChange = () => {
    if (activeTab.value === 'users') fetchUsers()
    if (activeTab.value === 'logins') {
        loginPage.value = 1
        fetchLoginLogs()
    }
    if (activeTab.value === 'ai') {
        aiPage.value = 1
        fetchAiLogs()
    }
    if (activeTab.value === 'ai_content') {
        aiContentPage.value = 1
        fetchAiUserStats()
        fetchAiContentLogs()
    }
}

watch(loginPage, () => fetchLoginLogs())
watch(aiPage, () => fetchAiLogs())
watch(aiContentPage, () => {
    if (activeTab.value === 'ai_content') fetchAiContentLogs()
})

onMounted(() => {
    fetchUsers()
})
</script>

<template>
<div class="fixed inset-0 bg-white z-50 overflow-y-auto">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div class="flex items-center justify-between mb-8 gap-4 flex-wrap">
            <h1 class="text-2xl font-bold text-gray-900 flex items-center gap-2">
                <el-icon><DataLine /></el-icon>
                系统后台管理
            </h1>
            <div class="flex items-center gap-3 flex-wrap">
                <el-button type="primary" :icon="Download" :loading="exportLoading" @click="downloadAllUserData">
                    导出全部用户数据
                </el-button>
                <el-button @click="$emit('close')">返回创作室</el-button>
            </div>
        </div>

        <el-tabs v-model="activeTab" @tab-change="handleTabChange" type="card">
            <el-tab-pane label="用户管理" name="users">
                <el-table :data="users" stripe v-loading="loading">
                    <el-table-column prop="id" label="ID" width="80" />
                    <el-table-column prop="username" label="用户名" />
                    <el-table-column label="角色">
                        <template #default="scope">
                            <el-tag :type="scope.row.is_admin ? 'danger' : 'info'">
                                {{ scope.row.is_admin ? '管理员' : '普通用户' }}
                            </el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="操作" width="150">
                        <template #default="scope">
                            <el-button
                                link
                                :type="scope.row.is_admin ? 'danger' : 'primary'"
                                :loading="roleUpdatingId === scope.row.id"
                                :disabled="scope.row.id === props.currentUserId && Boolean(scope.row.is_admin)"
                                @click="updateUserRole(scope.row)"
                            >
                                {{ scope.row.is_admin ? '取消管理员' : '设为管理员' }}
                            </el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </el-tab-pane>

            <el-tab-pane label="登录日志" name="logins">
                <el-table :data="loginLogs" stripe v-loading="loading">
                    <el-table-column prop="timestamp" label="时间" width="200">
                        <template #default="scope">
                            {{ new Date(scope.row.timestamp).toLocaleString() }}
                        </template>
                    </el-table-column>
                    <el-table-column prop="user_name" label="用户" width="120" />
                    <el-table-column prop="ip_address" label="IP 地址" width="160" />
                    <el-table-column prop="user_agent" label="设备信息" min-width="220">
                        <template #default="scope">
                            <span class="text-xs text-gray-500 break-words">{{ scope.row.user_agent || 'Unknown' }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="状态" width="100">
                        <template #default="scope">
                            <el-tag :type="scope.row.status === 'success' ? 'success' : 'danger'">
                                {{ scope.row.status }}
                            </el-tag>
                        </template>
                    </el-table-column>
                </el-table>
                <div class="mt-4 flex justify-center">
                    <el-pagination
                        v-model:current-page="loginPage"
                        layout="total, prev, pager, next"
                        :total="loginTotal"
                        :page-size="loginPageSize"
                        background
                    />
                </div>
            </el-tab-pane>

            <el-tab-pane label="AI 审计日志" name="ai">
                <el-table :data="aiLogs" stripe v-loading="loading">
                    <el-table-column prop="timestamp" label="时间" width="180" />
                    <el-table-column prop="user_name" label="用户" width="120" />
                    <el-table-column label="状态" width="100">
                        <template #default="scope">
                            <el-tag :type="getAiStatusType(scope.row.status)">
                                {{ scope.row.status || 'success' }}
                            </el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column prop="action" label="操作" width="150" />
                    <el-table-column prop="step_key" label="步骤" width="140">
                        <template #default="scope">
                            <span>{{ scope.row.step_key || '-' }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column prop="attempt" label="重试次数" width="100" />
                    <el-table-column prop="error_type" label="错误类型" width="150">
                        <template #default="scope">
                            <span class="text-xs break-words">{{ scope.row.error_type || '-' }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column prop="tokens" label="Tokens" width="100" />
                    <el-table-column label="失败原因" min-width="220">
                        <template #default="scope">
                            <el-popover placement="top" :width="420" trigger="hover">
                                <template #reference>
                                    <div class="truncate w-52 cursor-pointer text-red-500">
                                        {{ scope.row.error_message || '-' }}
                                    </div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-60 overflow-y-auto">{{ scope.row.error_message || '无' }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                    <el-table-column label="Prompt 摘要">
                        <template #default="scope">
                            <el-popover placement="top" :width="400" trigger="hover">
                                <template #reference>
                                    <div class="truncate w-40 cursor-pointer text-gray-500">{{ scope.row.prompt }}</div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-60 overflow-y-auto">{{ scope.row.prompt }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                    <el-table-column label="Response 摘要">
                        <template #default="scope">
                            <el-popover placement="top" :width="400" trigger="hover">
                                <template #reference>
                                    <div class="truncate w-40 cursor-pointer" :class="scope.row.status === 'failed' ? 'text-red-500' : 'text-blue-500'">
                                        {{ scope.row.response || '-' }}
                                    </div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-60 overflow-y-auto">{{ scope.row.response || '无' }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                </el-table>
                <div class="mt-4 flex justify-center">
                    <el-pagination
                        v-model:current-page="aiPage"
                        layout="total, prev, pager, next"
                        :total="aiTotal"
                        :page-size="aiPageSize"
                        background
                    />
                </div>
            </el-tab-pane>

            <el-tab-pane label="AI 内容审计" name="ai_content">
                <div class="mb-4 flex flex-wrap gap-3 items-center">
                    <el-select
                        v-model="aiContentUserId"
                        clearable
                        filterable
                        class="!w-64"
                        placeholder="按用户筛选"
                    >
                        <el-option
                            v-for="item in aiUserStats"
                            :key="item.user_id"
                            :label="`${item.username}（${item.log_count}）`"
                            :value="item.user_id"
                        />
                    </el-select>
                    <el-input
                        v-model="aiContentKeyword"
                        class="!w-96"
                        clearable
                        placeholder="关键词搜索（Prompt / Response / 错误信息 / 操作）"
                        @keyup.enter="applyAiContentFilter"
                    />
                    <el-button type="primary" @click="applyAiContentFilter">查询</el-button>
                    <el-button @click="resetAiContentFilter">重置</el-button>
                </div>

                <el-table :data="aiContentLogs" stripe v-loading="loading">
                    <el-table-column prop="timestamp" label="时间" width="180" />
                    <el-table-column prop="user_name" label="用户" width="120" />
                    <el-table-column prop="action" label="操作" width="160" />
                    <el-table-column label="状态" width="100">
                        <template #default="scope">
                            <el-tag :type="getAiStatusType(scope.row.status)">
                                {{ scope.row.status || 'success' }}
                            </el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column prop="tokens" label="Tokens" width="90" />
                    <el-table-column label="Prompt" min-width="260">
                        <template #default="scope">
                            <el-popover placement="top" :width="520" trigger="hover">
                                <template #reference>
                                    <div class="truncate cursor-pointer text-gray-600">
                                        {{ scope.row.prompt || '-' }}
                                    </div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-72 overflow-y-auto">{{ scope.row.prompt || '无' }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                    <el-table-column label="Response" min-width="260">
                        <template #default="scope">
                            <el-popover placement="top" :width="520" trigger="hover">
                                <template #reference>
                                    <div class="truncate cursor-pointer" :class="scope.row.status === 'failed' ? 'text-red-500' : 'text-blue-600'">
                                        {{ scope.row.response || '-' }}
                                    </div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-72 overflow-y-auto">{{ scope.row.response || '无' }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                    <el-table-column label="详情" width="90" fixed="right">
                        <template #default="scope">
                            <el-button size="small" link type="primary" @click="openAiDetail(scope.row.id)">查看</el-button>
                        </template>
                    </el-table-column>
                </el-table>

                <div class="mt-4 flex justify-center">
                    <el-pagination
                        v-model:current-page="aiContentPage"
                        layout="total, prev, pager, next"
                        :total="aiContentTotal"
                        :page-size="aiContentPageSize"
                        background
                    />
                </div>
            </el-tab-pane>
        </el-tabs>
    </div>

    <el-dialog v-model="aiDetailVisible" width="80%" title="AI 日志完整内容" destroy-on-close>
        <div v-loading="aiDetailLoading" class="min-h-40">
            <template v-if="aiDetail">
                <div class="grid grid-cols-2 gap-3 mb-4 text-sm">
                    <div><span class="text-gray-400">用户：</span>{{ aiDetail.user_name || '-' }}</div>
                    <div><span class="text-gray-400">时间：</span>{{ aiDetail.timestamp || '-' }}</div>
                    <div><span class="text-gray-400">操作：</span>{{ aiDetail.action || '-' }}</div>
                    <div><span class="text-gray-400">状态：</span>{{ aiDetail.status || 'success' }}</div>
                    <div><span class="text-gray-400">步骤：</span>{{ aiDetail.step_key || '-' }}</div>
                    <div><span class="text-gray-400">Tokens：</span>{{ aiDetail.tokens || 0 }}</div>
                </div>

                <div class="mb-3">
                    <div class="font-semibold mb-1">Prompt（完整）</div>
                    <div class="whitespace-pre-wrap text-xs leading-5 p-3 rounded border bg-gray-50 max-h-80 overflow-y-auto">{{ aiDetail.prompt || '无' }}</div>
                </div>
                <div>
                    <div class="font-semibold mb-1">Response（完整）</div>
                    <div class="whitespace-pre-wrap text-xs leading-5 p-3 rounded border bg-gray-50 max-h-80 overflow-y-auto">{{ aiDetail.response || '无' }}</div>
                </div>
            </template>
        </div>
    </el-dialog>
</div>
</template>
