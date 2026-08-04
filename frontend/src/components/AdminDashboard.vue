<script setup lang="ts">
import { reactive, ref, onMounted, watch } from 'vue'
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
const aiConfigLoading = ref(false)
const aiConfigSaving = ref(false)
const aiConfigTesting = ref(false)
const aiModelsLoading = ref(false)
const aiModelOptions = ref<string[]>([])
const aiConfigForm = reactive({
    base_url: '',
    model_id: '',
    api_key: '',
    timeout_seconds: 90,
    max_concurrency: 5,
    api_protocol: 'chat_completions',
    stream_response: false,
})
const aiConfigMeta = reactive({
    api_key_configured: false,
    api_key_masked: '',
    source: 'environment',
    updated_at: '',
    updated_by: '',
})
const aiProfiles = ref<any[]>([])
const aiRoutes = reactive<Record<string, string[]>>({})
const activeAiProfile = ref('')
const profileDialogVisible = ref(false)
const profileSaving = ref(false)
const profileModelsLoading = ref(false)
const profileModelOptions = ref<string[]>([])
const profileForm = reactive({
    profile_id: '',
    name: '',
    base_url: '',
    model_id: '',
    api_key: '',
    timeout_seconds: 90,
    max_concurrency: 5,
    api_protocol: 'chat_completions',
    stream_response: false,
    enabled: true,
    priority: 100,
})
const operationsLoading = ref(false)
const operationJobs = ref<any[]>([])
const operationAlerts = reactive({ counts: {} as Record<string, number>, recent_failures: [] as any[] })
const backups = ref<any[]>([])
const backupCreating = ref(false)
const backupSettings = reactive({
    enabled: false,
    interval_hours: 24,
    retention_count: 14,
    encrypt: true,
    mirror_directory: '',
})
const usageItems = ref<any[]>([])
const quotaSavingId = ref<number | null>(null)
const promptTemplates = ref<any[]>([])
const promptDialogVisible = ref(false)
const promptSaving = ref(false)
const promptForm = reactive({
    id: null as number | null,
    name: '',
    stage: 'outline',
    project_type: 'all',
    content: '',
    enabled: true,
})

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

const getApiErrorMessage = (error: any, fallback: string) => {
    const detail = error?.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
    return fallback
}

const applyAiConfigResponse = (data: any) => {
    aiConfigForm.base_url = String(data?.base_url || '')
    aiConfigForm.model_id = String(data?.model_id || '')
    aiModelOptions.value = aiConfigForm.model_id ? [aiConfigForm.model_id] : []
    aiConfigForm.api_key = ''
    aiConfigForm.timeout_seconds = Number(data?.timeout_seconds || 90)
    aiConfigForm.max_concurrency = Number(data?.max_concurrency || 5)
    aiConfigForm.api_protocol = String(data?.api_protocol || 'chat_completions')
    aiConfigForm.stream_response = Boolean(data?.stream_response)
    aiConfigMeta.api_key_configured = Boolean(data?.api_key_configured)
    aiConfigMeta.api_key_masked = String(data?.api_key_masked || '')
    aiConfigMeta.source = String(data?.source || 'environment')
    aiConfigMeta.updated_at = String(data?.updated_at || '')
    aiConfigMeta.updated_by = String(data?.updated_by || '')
}

const fetchAiConfig = async () => {
    aiConfigLoading.value = true
    try {
        const response = await api.get('/admin/ai-config')
        applyAiConfigResponse(response.data)
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '无法获取 AI 配置'))
    } finally {
        aiConfigLoading.value = false
    }
}

const validateAiConfig = () => {
    if (!aiConfigForm.base_url.trim() || !aiConfigForm.model_id.trim()) {
        ElMessage.warning('请完整填写 Base URL 和模型 ID')
        return false
    }
    if (!aiConfigMeta.api_key_configured && !aiConfigForm.api_key.trim()) {
        ElMessage.warning('请填写 API Key')
        return false
    }
    return true
}

const buildAiConfigPayload = (clearApiKey = false) => ({
    base_url: aiConfigForm.base_url.trim(),
    model_id: aiConfigForm.model_id.trim(),
    api_key: aiConfigForm.api_key.trim() || null,
    clear_api_key: clearApiKey,
    timeout_seconds: aiConfigForm.timeout_seconds,
    max_concurrency: aiConfigForm.max_concurrency,
    api_protocol: aiConfigForm.api_protocol,
    stream_response: aiConfigForm.stream_response,
})

const saveAiConfig = async () => {
    if (!validateAiConfig()) return
    aiConfigSaving.value = true
    try {
        const response = await api.put('/admin/ai-config', buildAiConfigPayload())
        applyAiConfigResponse(response.data)
        ElMessage.success('AI 配置已保存，将从下一次生成请求开始生效')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '保存 AI 配置失败'))
    } finally {
        aiConfigSaving.value = false
    }
}

const testAiConfig = async () => {
    if (!validateAiConfig()) return
    aiConfigTesting.value = true
    try {
        const response = await api.post('/admin/ai-config/test', buildAiConfigPayload())
        const preview = String(response.data?.response_preview || '').trim()
        ElMessage.success(preview ? `连接成功：${preview}` : 'AI 连接测试成功')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, 'AI 连接测试失败'))
    } finally {
        aiConfigTesting.value = false
    }
}

const fetchAvailableModels = async (target: 'main' | 'profile') => {
    const form = target === 'main' ? aiConfigForm : profileForm
    if (!form.base_url.trim()) {
        ElMessage.warning('请先填写 Base URL')
        return
    }
    const loadingRef = target === 'main' ? aiModelsLoading : profileModelsLoading
    loadingRef.value = true
    try {
        const response = await api.post('/admin/ai-config/models', {
            base_url: form.base_url.trim(),
            api_key: form.api_key.trim() || null,
            profile_id: target === 'profile' ? profileForm.profile_id.trim() : null,
            timeout_seconds: form.timeout_seconds,
        })
        const models = Array.isArray(response.data?.models)
            ? response.data.models.map((item: any) => String(item)).filter(Boolean)
            : []
        if (target === 'main') {
            aiModelOptions.value = models
            if (!aiConfigForm.model_id && models.length) aiConfigForm.model_id = models[0]
        } else {
            profileModelOptions.value = models
            if (!profileForm.model_id && models.length) profileForm.model_id = models[0]
        }
        ElMessage.success(`已获取 ${models.length} 个模型`)
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '获取模型列表失败'))
    } finally {
        loadingRef.value = false
    }
}

const clearAiApiKey = async () => {
    try {
        await ElMessageBox.confirm(
            '清除后 AI 生成功能将不可用，确定继续吗？',
            '清除 API Key',
            { type: 'warning', confirmButtonText: '确定清除', cancelButtonText: '取消' },
        )
        aiConfigSaving.value = true
        const response = await api.put('/admin/ai-config', buildAiConfigPayload(true))
        applyAiConfigResponse(response.data)
        ElMessage.success('API Key 已清除')
    } catch (error: any) {
        if (error !== 'cancel' && error !== 'close') {
            ElMessage.error(getApiErrorMessage(error, '清除 API Key 失败'))
        }
    } finally {
        aiConfigSaving.value = false
    }
}

const aiTaskTypes = [
    { value: 'planning', label: '故事规划' },
    { value: 'interaction', label: '交互设定' },
    { value: 'outline', label: '分场大纲' },
    { value: 'content', label: '正文生成' },
    { value: 'review', label: '内容审核' },
    { value: 'prompt', label: '提示词转写' },
]

const fetchAiProfiles = async () => {
    try {
        const response = await api.get('/admin/ai-profiles')
        aiProfiles.value = Array.isArray(response.data?.profiles) ? response.data.profiles : []
        activeAiProfile.value = String(response.data?.active_profile || '')
        Object.keys(aiRoutes).forEach((key) => delete aiRoutes[key])
        Object.assign(aiRoutes, response.data?.routes || {})
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '无法获取多模型配置'))
    }
}

const openProfileDialog = (profile?: any) => {
    profileForm.profile_id = String(profile?.profile_id || `model-${Date.now()}`)
    profileForm.name = String(profile?.profile_name || '')
    profileForm.base_url = String(profile?.base_url || aiConfigForm.base_url || '')
    profileForm.model_id = String(profile?.model_id || '')
    profileModelOptions.value = profileForm.model_id ? [profileForm.model_id] : []
    profileForm.api_key = ''
    profileForm.timeout_seconds = Number(profile?.timeout_seconds || 90)
    profileForm.max_concurrency = Number(profile?.max_concurrency || 5)
    profileForm.api_protocol = String(profile?.api_protocol || 'chat_completions')
    profileForm.stream_response = Boolean(profile?.stream_response)
    profileForm.enabled = profile?.enabled !== false
    profileForm.priority = Number(profile?.priority ?? 100)
    profileDialogVisible.value = true
}

const saveAiProfile = async () => {
    if (!profileForm.profile_id.trim() || !profileForm.name.trim() || !profileForm.base_url.trim() || !profileForm.model_id.trim()) {
        ElMessage.warning('请完整填写配置档案信息')
        return
    }
    profileSaving.value = true
    try {
        await api.put(`/admin/ai-profiles/${encodeURIComponent(profileForm.profile_id.trim())}`, {
            name: profileForm.name.trim(),
            base_url: profileForm.base_url.trim(),
            model_id: profileForm.model_id.trim(),
            api_key: profileForm.api_key.trim() || null,
            clear_api_key: false,
            timeout_seconds: profileForm.timeout_seconds,
            max_concurrency: profileForm.max_concurrency,
            api_protocol: profileForm.api_protocol,
            stream_response: profileForm.stream_response,
            enabled: profileForm.enabled,
            priority: profileForm.priority,
        })
        profileDialogVisible.value = false
        await fetchAiProfiles()
        await fetchAiConfig()
        ElMessage.success('AI 配置档案已保存')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '保存 AI 配置档案失败'))
    } finally {
        profileSaving.value = false
    }
}

const deleteAiProfile = async (profile: any) => {
    try {
        await ElMessageBox.confirm(`确定删除“${profile.profile_name}”吗？`, '删除 AI 配置档案', { type: 'warning' })
        await api.delete(`/admin/ai-profiles/${encodeURIComponent(profile.profile_id)}`)
        await fetchAiProfiles()
        await fetchAiConfig()
        ElMessage.success('配置档案已删除')
    } catch (error: any) {
        if (error !== 'cancel' && error !== 'close') {
            ElMessage.error(getApiErrorMessage(error, '删除配置档案失败'))
        }
    }
}

const saveAiRouting = async () => {
    try {
        await api.put('/admin/ai-routing', {
            active_profile: activeAiProfile.value,
            routes: Object.fromEntries(
                aiTaskTypes.map((item) => [item.value, aiRoutes[item.value] || []]),
            ),
        })
        await fetchAiProfiles()
        await fetchAiConfig()
        ElMessage.success('模型路由与故障切换顺序已保存')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '保存模型路由失败'))
    }
}

const fetchOperations = async () => {
    operationsLoading.value = true
    try {
        const [jobsResponse, alertsResponse, backupsResponse, settingsResponse] = await Promise.all([
            api.get('/admin/ops/jobs'),
            api.get('/admin/ops/alerts'),
            api.get('/admin/ops/backups'),
            api.get('/admin/ops/backup-settings'),
        ])
        operationJobs.value = jobsResponse.data?.items || []
        operationAlerts.counts = alertsResponse.data?.counts || {}
        operationAlerts.recent_failures = alertsResponse.data?.recent_failures || []
        backups.value = backupsResponse.data?.items || []
        Object.assign(backupSettings, settingsResponse.data || {})
        backupSettings.mirror_directory = String(settingsResponse.data?.mirror_directory || '')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '无法获取运维数据'))
    } finally {
        operationsLoading.value = false
    }
}

const saveBackupSettings = async () => {
    try {
        await api.put('/admin/ops/backup-settings', {
            ...backupSettings,
            mirror_directory: backupSettings.mirror_directory.trim() || null,
        })
        ElMessage.success('定时备份设置已保存')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '保存备份设置失败'))
    }
}

const createServerBackup = async () => {
    backupCreating.value = true
    try {
        await api.post('/admin/ops/backups')
        await fetchOperations()
        ElMessage.success('服务器备份已创建')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '创建服务器备份失败'))
    } finally {
        backupCreating.value = false
    }
}

const downloadServerBackup = async (item: any) => {
    try {
        const response = await api.get(`/admin/ops/backups/${item.id}/download`, { responseType: 'blob' })
        const url = window.URL.createObjectURL(new Blob([response.data]))
        const link = document.createElement('a')
        link.href = url
        link.download = item.filename
        link.click()
        window.URL.revokeObjectURL(url)
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '下载备份失败'))
    }
}

const restoreServerBackup = async (item: any) => {
    try {
        await ElMessageBox.confirm(
            '备份中的项目将以“恢复副本”形式导入，不覆盖当前项目。确定继续吗？',
            '恢复服务器备份',
            { type: 'warning' },
        )
        const response = await api.post(`/admin/ops/backups/${item.id}/restore`, { confirm: true })
        ElMessage.success(`已恢复 ${response.data?.project_count || 0} 个项目副本`)
    } catch (error: any) {
        if (error !== 'cancel' && error !== 'close') {
            ElMessage.error(getApiErrorMessage(error, '恢复备份失败'))
        }
    }
}

const adminCancelJob = async (item: any) => {
    try {
        await api.post(`/admin/ops/jobs/${item.id}/cancel`)
        await fetchOperations()
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '取消任务失败'))
    }
}

const adminRetryJob = async (item: any) => {
    try {
        await api.post(`/admin/ops/jobs/${item.id}/retry`)
        await fetchOperations()
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '重试任务失败'))
    }
}

const fetchUsage = async () => {
    loading.value = true
    try {
        const response = await api.get('/admin/ops/usage')
        usageItems.value = response.data?.items || []
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '无法获取用量数据'))
    } finally {
        loading.value = false
    }
}

const saveUserQuota = async (item: any) => {
    quotaSavingId.value = item.user_id
    try {
        await api.patch(`/admin/ops/users/${item.user_id}/quota`, {
            daily_token_limit: Number(item.daily_limit || 0),
            monthly_token_limit: Number(item.monthly_limit || 0),
        })
        ElMessage.success('用户额度已保存')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '保存用户额度失败'))
    } finally {
        quotaSavingId.value = null
    }
}

const fetchPromptTemplates = async () => {
    loading.value = true
    try {
        const response = await api.get('/admin/ops/prompt-templates')
        promptTemplates.value = response.data?.items || []
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '无法获取 Prompt 模板'))
    } finally {
        loading.value = false
    }
}

const openPromptDialog = (item?: any) => {
    promptForm.id = item?.id || null
    promptForm.name = String(item?.name || '')
    promptForm.stage = String(item?.stage || 'outline')
    promptForm.project_type = String(item?.project_type || 'all')
    promptForm.content = String(item?.content || '')
    promptForm.enabled = item?.enabled !== false
    promptDialogVisible.value = true
}

const savePromptTemplate = async () => {
    if (!promptForm.name.trim() || !promptForm.content.trim()) {
        ElMessage.warning('请填写模板名称和内容')
        return
    }
    promptSaving.value = true
    try {
        const payload = {
            name: promptForm.name.trim(),
            stage: promptForm.stage,
            project_type: promptForm.project_type,
            content: promptForm.content,
            enabled: promptForm.enabled,
        }
        if (promptForm.id) {
            await api.put(`/admin/ops/prompt-templates/${promptForm.id}`, payload)
        } else {
            await api.post('/admin/ops/prompt-templates', payload)
        }
        promptDialogVisible.value = false
        await fetchPromptTemplates()
        ElMessage.success('Prompt 模板已保存')
    } catch (error: any) {
        ElMessage.error(getApiErrorMessage(error, '保存 Prompt 模板失败'))
    } finally {
        promptSaving.value = false
    }
}

const deletePromptTemplate = async (item: any) => {
    try {
        await ElMessageBox.confirm(`确定删除模板“${item.name}”吗？`, '删除模板', { type: 'warning' })
        await api.delete(`/admin/ops/prompt-templates/${item.id}`)
        await fetchPromptTemplates()
    } catch (error: any) {
        if (error !== 'cancel' && error !== 'close') {
            ElMessage.error(getApiErrorMessage(error, '删除模板失败'))
        }
    }
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
    if (activeTab.value === 'ai_config') {
        fetchAiConfig()
        fetchAiProfiles()
    }
    if (activeTab.value === 'operations') fetchOperations()
    if (activeTab.value === 'usage') fetchUsage()
    if (activeTab.value === 'prompts') fetchPromptTemplates()
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

            <el-tab-pane label="AI 配置" name="ai_config">
                <div v-loading="aiConfigLoading" class="max-w-3xl py-3">
                    <el-alert
                        title="配置保存后无需重启服务，API 与生成 Worker 会在下一次请求时自动使用新配置。"
                        type="info"
                        :closable="false"
                        show-icon
                        class="mb-6"
                    />

                    <el-form label-position="top">
                        <el-form-item label="接口类型">
                            <div class="flex items-center gap-3">
                                <el-tag type="primary">OpenAI 兼容接口</el-tag>
                                <span class="text-xs text-gray-400">支持 OpenAI、兼容网关及私有模型服务</span>
                            </div>
                        </el-form-item>

                        <el-form-item label="接口协议" required>
                            <el-select v-model="aiConfigForm.api_protocol" class="!w-full">
                                <el-option label="Chat Completions（/chat/completions）" value="chat_completions" />
                                <el-option label="Responses API（/responses，Codex 渠道）" value="responses" />
                            </el-select>
                            <span class="text-xs text-gray-400 mt-1">
                                遇到 “/v1/chat/completions endpoint not supported” 时请选择 Responses API。
                            </span>
                        </el-form-item>

                        <el-form-item label="Base URL" required>
                            <el-input
                                v-model="aiConfigForm.base_url"
                                maxlength="2048"
                                placeholder="例如：https://api.openai.com/v1"
                            />
                        </el-form-item>

                        <el-form-item label="模型 ID" required>
                            <div class="flex gap-2 w-full">
                                <el-select
                                    v-model="aiConfigForm.model_id"
                                    filterable
                                    allow-create
                                    default-first-option
                                    class="flex-1"
                                    placeholder="获取模型或直接输入模型 ID"
                                >
                                    <el-option v-for="model in aiModelOptions" :key="model" :label="model" :value="model" />
                                </el-select>
                                <el-button :loading="aiModelsLoading" @click="fetchAvailableModels('main')">获取模型</el-button>
                            </div>
                        </el-form-item>

                        <el-form-item label="API Key" :required="!aiConfigMeta.api_key_configured">
                            <el-input
                                v-model="aiConfigForm.api_key"
                                type="password"
                                show-password
                                autocomplete="new-password"
                                maxlength="4096"
                                :placeholder="aiConfigMeta.api_key_configured ? `已配置 ${aiConfigMeta.api_key_masked}；留空则保持不变` : '请输入 API Key'"
                            />
                            <div class="w-full flex items-center justify-between mt-2 gap-4">
                                <span class="text-xs text-gray-400">出于安全考虑，后台不会返回 API Key 明文。</span>
                                <el-button
                                    v-if="aiConfigMeta.api_key_configured"
                                    link
                                    type="danger"
                                    :disabled="aiConfigSaving"
                                    @click="clearAiApiKey"
                                >
                                    清除密钥
                                </el-button>
                            </div>
                        </el-form-item>

                        <div class="grid grid-cols-1 sm:grid-cols-3 gap-5">
                            <el-form-item label="请求超时（秒）">
                                <el-input-number
                                    v-model="aiConfigForm.timeout_seconds"
                                    :min="10"
                                    :max="600"
                                    :step="10"
                                    class="!w-full"
                                />
                            </el-form-item>
                            <el-form-item label="最大并发请求数">
                                <el-input-number
                                    v-model="aiConfigForm.max_concurrency"
                                    :min="1"
                                    :max="20"
                                    class="!w-full"
                                />
                            </el-form-item>
                            <el-form-item label="仅流式响应">
                                <el-switch v-model="aiConfigForm.stream_response" />
                            </el-form-item>
                        </div>

                        <div class="flex items-center gap-3 flex-wrap mt-2">
                            <el-button type="primary" :loading="aiConfigSaving" @click="saveAiConfig">
                                保存配置
                            </el-button>
                            <el-button :loading="aiConfigTesting" @click="testAiConfig">
                                测试连接
                            </el-button>
                            <span class="text-xs text-gray-400">
                                当前来源：{{ aiConfigMeta.source === 'admin' ? '管理后台配置' : '服务器环境变量' }}
                                <template v-if="aiConfigMeta.updated_at">
                                    · {{ new Date(aiConfigMeta.updated_at).toLocaleString() }}
                                </template>
                                <template v-if="aiConfigMeta.updated_by">
                                    · {{ aiConfigMeta.updated_by }}
                                </template>
                            </span>
                        </div>
                    </el-form>

                    <el-divider content-position="left">多模型档案与故障切换</el-divider>
                    <div class="flex justify-between items-center mb-3 gap-3 flex-wrap">
                        <span class="text-sm text-gray-500">同一任务会按路由顺序尝试模型，失败后自动切换到下一个档案。</span>
                        <el-button type="primary" plain @click="openProfileDialog()">新增配置档案</el-button>
                    </div>
                    <el-table :data="aiProfiles" size="small" border>
                        <el-table-column prop="profile_name" label="名称" min-width="130" />
                        <el-table-column prop="model_id" label="模型" min-width="150" />
                        <el-table-column label="协议" width="120">
                            <template #default="scope">{{ scope.row.api_protocol === 'responses' ? 'Responses' : 'Chat' }}</template>
                        </el-table-column>
                        <el-table-column prop="base_url" label="Base URL" min-width="220" show-overflow-tooltip />
                        <el-table-column label="密钥" width="100">
                            <template #default="scope">
                                <el-tag :type="scope.row.api_key_configured ? 'success' : 'danger'" size="small">
                                    {{ scope.row.api_key_configured ? '已配置' : '未配置' }}
                                </el-tag>
                            </template>
                        </el-table-column>
                        <el-table-column prop="priority" label="优先级" width="85" />
                        <el-table-column label="响应" width="85">
                            <template #default="scope">{{ scope.row.stream_response ? '流式' : '普通' }}</template>
                        </el-table-column>
                        <el-table-column label="状态" width="80">
                            <template #default="scope">{{ scope.row.enabled ? '启用' : '停用' }}</template>
                        </el-table-column>
                        <el-table-column label="操作" width="140" fixed="right">
                            <template #default="scope">
                                <el-button link type="primary" @click="openProfileDialog(scope.row)">编辑</el-button>
                                <el-button link type="danger" @click="deleteAiProfile(scope.row)">删除</el-button>
                            </template>
                        </el-table-column>
                    </el-table>

                    <div class="mt-5 p-4 rounded border bg-gray-50">
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <el-form-item label="默认配置档案" class="!mb-0">
                                <el-select v-model="activeAiProfile" class="!w-full">
                                    <el-option v-for="item in aiProfiles" :key="item.profile_id" :label="item.profile_name" :value="item.profile_id" />
                                </el-select>
                            </el-form-item>
                            <el-form-item v-for="task in aiTaskTypes" :key="task.value" :label="`${task.label}路由顺序`" class="!mb-0">
                                <el-select v-model="aiRoutes[task.value]" multiple class="!w-full" placeholder="默认档案 + 自动故障切换">
                                    <el-option v-for="item in aiProfiles" :key="item.profile_id" :label="item.profile_name" :value="item.profile_id" />
                                </el-select>
                            </el-form-item>
                        </div>
                        <el-button class="mt-4" type="primary" @click="saveAiRouting">保存模型路由</el-button>
                    </div>
                </div>
            </el-tab-pane>

            <el-tab-pane label="运维中心" name="operations">
                <div v-loading="operationsLoading">
                    <div class="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-6">
                        <div v-for="(count, status) in operationAlerts.counts" :key="status" class="rounded border p-3 bg-gray-50">
                            <div class="text-xs text-gray-400">{{ status }}</div>
                            <div class="text-2xl font-bold mt-1">{{ count }}</div>
                        </div>
                    </div>

                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-semibold">生成任务监控</h3>
                        <el-button @click="fetchOperations">刷新</el-button>
                    </div>
                    <el-table :data="operationJobs" size="small" border max-height="360">
                        <el-table-column prop="id" label="任务" width="70" />
                        <el-table-column prop="project_title" label="项目" min-width="150" show-overflow-tooltip />
                        <el-table-column prop="kind" label="类型" width="150" />
                        <el-table-column prop="status" label="状态" width="100" />
                        <el-table-column label="尝试" width="80">
                            <template #default="scope">{{ scope.row.attempts }}/{{ scope.row.max_attempts }}</template>
                        </el-table-column>
                        <el-table-column prop="last_error" label="错误" min-width="220" show-overflow-tooltip />
                        <el-table-column label="操作" width="150" fixed="right">
                            <template #default="scope">
                                <el-button v-if="['queued', 'running'].includes(scope.row.status)" link type="danger" @click="adminCancelJob(scope.row)">取消</el-button>
                                <el-button v-if="['failed', 'canceled'].includes(scope.row.status)" link type="primary" @click="adminRetryJob(scope.row)">重试</el-button>
                            </template>
                        </el-table-column>
                    </el-table>

                    <el-divider content-position="left">服务器备份与恢复</el-divider>
                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
                        <el-form-item label="启用定时备份" class="!mb-0"><el-switch v-model="backupSettings.enabled" /></el-form-item>
                        <el-form-item label="间隔（小时）" class="!mb-0"><el-input-number v-model="backupSettings.interval_hours" :min="1" :max="720" class="!w-full" /></el-form-item>
                        <el-form-item label="保留份数" class="!mb-0"><el-input-number v-model="backupSettings.retention_count" :min="1" :max="365" class="!w-full" /></el-form-item>
                        <el-form-item label="加密保存" class="!mb-0"><el-switch v-model="backupSettings.encrypt" /></el-form-item>
                    </div>
                    <el-form-item label="异地镜像目录（可填已挂载的 NAS/云盘目录）">
                        <el-input v-model="backupSettings.mirror_directory" placeholder="留空则只保存在本机 backups/server" />
                    </el-form-item>
                    <div class="flex gap-3 mb-4">
                        <el-button type="primary" @click="saveBackupSettings">保存备份设置</el-button>
                        <el-button :loading="backupCreating" @click="createServerBackup">立即创建备份</el-button>
                    </div>
                    <el-table :data="backups" size="small" border max-height="320">
                        <el-table-column prop="created_at" label="时间" min-width="180" />
                        <el-table-column prop="filename" label="文件" min-width="260" show-overflow-tooltip />
                        <el-table-column prop="backup_type" label="类型" width="100" />
                        <el-table-column label="大小" width="110"><template #default="scope">{{ Math.ceil(scope.row.size_bytes / 1024) }} KB</template></el-table-column>
                        <el-table-column label="操作" width="150" fixed="right">
                            <template #default="scope">
                                <el-button link type="primary" @click="downloadServerBackup(scope.row)">下载</el-button>
                                <el-button link type="warning" @click="restoreServerBackup(scope.row)">恢复副本</el-button>
                            </template>
                        </el-table-column>
                    </el-table>
                </div>
            </el-tab-pane>

            <el-tab-pane label="用量与额度" name="usage">
                <el-alert title="额度填 0 表示不限制；达到日额度或月额度后将暂停新的 AI 请求。" type="info" :closable="false" class="mb-4" />
                <el-table :data="usageItems" stripe v-loading="loading">
                    <el-table-column prop="username" label="用户" min-width="130" />
                    <el-table-column prop="daily_tokens" label="今日 Tokens" min-width="120" />
                    <el-table-column prop="monthly_tokens" label="本月 Tokens" min-width="120" />
                    <el-table-column label="每日额度" min-width="170">
                        <template #default="scope"><el-input-number v-model="scope.row.daily_limit" :min="0" :max="2000000000" class="!w-full" /></template>
                    </el-table-column>
                    <el-table-column label="每月额度" min-width="170">
                        <template #default="scope"><el-input-number v-model="scope.row.monthly_limit" :min="0" :max="2000000000" class="!w-full" /></template>
                    </el-table-column>
                    <el-table-column label="操作" width="100">
                        <template #default="scope"><el-button link type="primary" :loading="quotaSavingId === scope.row.user_id" @click="saveUserQuota(scope.row)">保存</el-button></template>
                    </el-table-column>
                </el-table>
            </el-tab-pane>

            <el-tab-pane label="Prompt 模板" name="prompts">
                <div class="flex justify-between items-center mb-4">
                    <span class="text-sm text-gray-500">启用后会自动追加到对应剧本类型和生成阶段。</span>
                    <el-button type="primary" @click="openPromptDialog()">新增模板</el-button>
                </div>
                <el-table :data="promptTemplates" stripe v-loading="loading">
                    <el-table-column prop="name" label="名称" min-width="150" />
                    <el-table-column prop="stage" label="阶段" width="110" />
                    <el-table-column prop="project_type" label="剧本类型" width="110" />
                    <el-table-column prop="content" label="内容" min-width="260" show-overflow-tooltip />
                    <el-table-column label="状态" width="80"><template #default="scope">{{ scope.row.enabled ? '启用' : '停用' }}</template></el-table-column>
                    <el-table-column label="操作" width="130" fixed="right">
                        <template #default="scope">
                            <el-button link type="primary" @click="openPromptDialog(scope.row)">编辑</el-button>
                            <el-button link type="danger" @click="deletePromptTemplate(scope.row)">删除</el-button>
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

    <el-dialog v-model="profileDialogVisible" width="680px" title="AI 配置档案" destroy-on-close>
        <el-form label-position="top">
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <el-form-item label="档案 ID" required><el-input v-model="profileForm.profile_id" :disabled="aiProfiles.some((item) => item.profile_id === profileForm.profile_id)" /></el-form-item>
                <el-form-item label="显示名称" required><el-input v-model="profileForm.name" /></el-form-item>
            </div>
            <el-form-item label="Base URL" required><el-input v-model="profileForm.base_url" /></el-form-item>
            <el-form-item label="模型 ID" required>
                <div class="flex gap-2 w-full">
                    <el-select
                        v-model="profileForm.model_id"
                        filterable
                        allow-create
                        default-first-option
                        class="flex-1"
                        placeholder="获取模型或直接输入模型 ID"
                    >
                        <el-option v-for="model in profileModelOptions" :key="model" :label="model" :value="model" />
                    </el-select>
                    <el-button :loading="profileModelsLoading" @click="fetchAvailableModels('profile')">获取模型</el-button>
                </div>
            </el-form-item>
            <el-form-item label="接口协议" required>
                <el-select v-model="profileForm.api_protocol" class="!w-full">
                    <el-option label="Chat Completions（/chat/completions）" value="chat_completions" />
                    <el-option label="Responses API（/responses，Codex 渠道）" value="responses" />
                </el-select>
            </el-form-item>
            <el-form-item label="API Key"><el-input v-model="profileForm.api_key" type="password" show-password placeholder="编辑时留空则保持原密钥" /></el-form-item>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <el-form-item label="超时（秒）"><el-input-number v-model="profileForm.timeout_seconds" :min="10" :max="600" class="!w-full" /></el-form-item>
                <el-form-item label="最大并发"><el-input-number v-model="profileForm.max_concurrency" :min="1" :max="20" class="!w-full" /></el-form-item>
                <el-form-item label="优先级"><el-input-number v-model="profileForm.priority" :min="0" :max="10000" class="!w-full" /></el-form-item>
            </div>
            <div class="flex gap-8">
                <el-form-item label="启用"><el-switch v-model="profileForm.enabled" /></el-form-item>
                <el-form-item label="仅流式响应"><el-switch v-model="profileForm.stream_response" /></el-form-item>
            </div>
        </el-form>
        <template #footer>
            <el-button @click="profileDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="profileSaving" @click="saveAiProfile">保存</el-button>
        </template>
    </el-dialog>

    <el-dialog v-model="promptDialogVisible" width="720px" title="Prompt 模板" destroy-on-close>
        <el-form label-position="top">
            <el-form-item label="模板名称" required><el-input v-model="promptForm.name" /></el-form-item>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <el-form-item label="生成阶段" required>
                    <el-select v-model="promptForm.stage" class="!w-full">
                        <el-option label="分场大纲" value="outline" /><el-option label="正文生成" value="content" />
                        <el-option label="内容审核" value="review" /><el-option label="交互设定" value="interaction" /><el-option label="提示词转写" value="prompt" />
                    </el-select>
                </el-form-item>
                <el-form-item label="剧本类型">
                    <el-select v-model="promptForm.project_type" class="!w-full">
                        <el-option label="全部" value="all" /><el-option label="电影" value="movie" /><el-option label="剧集" value="tv" />
                        <el-option label="短剧" value="short" /><el-option label="短视频" value="short_video" />
                    </el-select>
                </el-form-item>
            </div>
            <el-form-item label="附加指令" required><el-input v-model="promptForm.content" type="textarea" :rows="10" maxlength="50000" show-word-limit /></el-form-item>
            <el-form-item label="启用"><el-switch v-model="promptForm.enabled" /></el-form-item>
        </el-form>
        <template #footer>
            <el-button @click="promptDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="promptSaving" @click="savePromptTemplate">保存</el-button>
        </template>
    </el-dialog>
</div>
</template>
