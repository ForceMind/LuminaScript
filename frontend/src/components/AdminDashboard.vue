<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { DataLine, Download } from '@element-plus/icons-vue'

const props = defineProps<{ token: string }>()
const emit = defineEmits(['close'])

const activeTab = ref('users')
const users = ref<any[]>([])
const loginLogs = ref<any[]>([])
const aiLogs = ref<any[]>([])
const loading = ref(false)
const exportLoading = ref(false)

const loginPage = ref(1)
const loginPageSize = ref(20)
const loginTotal = ref(0)

const aiPage = ref(1)
const aiPageSize = ref(20)
const aiTotal = ref(0)

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
}

watch(loginPage, () => fetchLoginLogs())
watch(aiPage, () => fetchAiLogs())

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
        </el-tabs>
    </div>
</div>
</template>
