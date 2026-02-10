<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { User, Timer, DataLine } from '@element-plus/icons-vue'

const props = defineProps<{ token: string }>()
const emit = defineEmits(['close'])

const activeTab = ref('users')
const users = ref([])
const loginLogs = ref([])
const aiLogs = ref([])
const loading = ref(false)

const api = axios.create({ baseURL: '/api' })
api.interceptors.request.use((config) => {
    config.headers.Authorization = `Bearer ${props.token}`
    return config
})

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
        const res = await api.get('/admin/logs/login')
        loginLogs.value = res.data
    } catch (e) {
        ElMessage.error('无法获取登录日志')
    } finally {
        loading.value = false
    }
}

const fetchAiLogs = async () => {
    loading.value = true
    try {
        const res = await api.get('/admin/logs/ai')
        aiLogs.value = res.data
    } catch (e) {
        ElMessage.error('无法获取AI日志')
    } finally {
        loading.value = false
    }
}

const handleTabChange = () => {
    if (activeTab.value === 'users') fetchUsers()
    if (activeTab.value === 'logins') fetchLoginLogs()
    if (activeTab.value === 'ai') fetchAiLogs()
}

onMounted(() => {
    fetchUsers()
})
</script>

<template>
<div class="fixed inset-0 bg-white z-50 overflow-y-auto">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div class="flex items-center justify-between mb-8">
            <h1 class="text-2xl font-bold text-gray-900 flex items-center gap-2">
                <el-icon><DataLine /></el-icon>
                系统后台管理
            </h1>
            <el-button @click="$emit('close')">返回创作室</el-button>
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
                    <el-table-column prop="timestamp" label="时间" width="200" />
                    <el-table-column prop="user_name" label="用户" width="150" />
                    <el-table-column prop="ip_address" label="IP 地址" width="150" />
                    <el-table-column label="状态">
                        <template #default="scope">
                            <el-tag :type="scope.row.status === 'success' ? 'success' : 'danger'">
                                {{ scope.row.status }}
                            </el-tag>
                        </template>
                    </el-table-column>
                </el-table>
            </el-tab-pane>

            <el-tab-pane label="AI 交互审计" name="ai">
                 <el-table :data="aiLogs" stripe v-loading="loading">
                    <el-table-column prop="timestamp" label="时间" width="180" />
                    <el-table-column prop="user_name" label="用户" width="120" />
                    <el-table-column prop="action" label="操作" width="150" />
                    <el-table-column prop="tokens" label="Tokens" width="100" />
                    <el-table-column label="Prompt (摘要)">
                        <template #default="scope">
                            <el-popover placement="top" :width="400" trigger="hover">
                                <template #reference>
                                <div class="truncate w-40 cursor-pointer text-gray-500">{{ scope.row.prompt }}</div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-60 overflow-y-auto">{{ scope.row.prompt }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                    <el-table-column label="Response (摘要)">
                        <template #default="scope">
                             <el-popover placement="top" :width="400" trigger="hover">
                                <template #reference>
                                <div class="truncate w-40 cursor-pointer text-blue-500">{{ scope.row.response }}</div>
                                </template>
                                <div class="whitespace-pre-wrap text-xs h-60 overflow-y-auto">{{ scope.row.response }}</div>
                            </el-popover>
                        </template>
                    </el-table-column>
                </el-table>
            </el-tab-pane>
        </el-tabs>
    </div>
</div>
</template>
