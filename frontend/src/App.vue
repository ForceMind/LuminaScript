<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import {
  Document,
  Edit,
  MagicStick,
  User,
  Monitor,
  Menu as IconMenu,
  Film,
  Plus,
  Loading,
  Delete,
  Coin,
  SwitchButton,
  DataLine,
  Download,
  ArrowDown
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { marked } from 'marked'
import AdminDashboard from './components/AdminDashboard.vue'

marked.setOptions({ gfm: true, breaks: true })

const toTextValue = (value: unknown): string => {
    if (value === null || value === undefined) return ''
    if (typeof value === 'string') return value
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
    try {
        return JSON.stringify(value, null, 2)
    } catch {
        return String(value)
    }
}

const escapeHtml = (text: string) => {
    return text
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;')
}

const renderMarkdown = (value: unknown) => {
    const text = toTextValue(value)
    if (!text) return ''
    try {
        return marked.parse(text) as string
    } catch (e) {
        console.error("Markdown parse error:", e)
        return text
    }
}

// --- State ---
const token = ref(localStorage.getItem('token') || '')
const user = ref<any>(null)
const drawerOpen = ref(false)
const showAdmin = ref(false)

// Auth Form
const isLoginMode = ref(true)
const authForm = ref({ username: '', password: '' })
const authLoading = ref(false)

// Project
const logline = ref('')
const currentProject = ref<any>(null)
const interaction = ref<any>(null)
const selectedOption = ref('')
const customInput = ref('')
const loading = ref(false)
const loadingText = ref('AI 正在思考中...')
const switchingProject = ref(false)
const projectList = ref<any[]>([])
const scenePromptMap = ref<Record<number, string>>({})
const scenePromptLoadingMap = ref<Record<number, boolean>>({})
const pollTimer = ref<any>(null)
const isStarted = ref(false)

const pollRequestInFlight = ref(false)
const ACTIVE_PROJECT_POLL_INTERVAL_MS = 8000
const BACKGROUND_LIST_POLL_INTERVAL_MS = 45000

const isDocumentVisible = () => typeof document === 'undefined' || document.visibilityState === 'visible'

const normalizeProjectStatus = (status: unknown) => {
    const raw = toTextValue(status).trim().toLowerCase()
    if (!raw) return ''
    if (raw.includes('.')) {
        const parts = raw.split('.')
        return parts[parts.length - 1] || raw
    }
    return raw
}

const isStatus = (status: unknown, expected: string) => {
    return normalizeProjectStatus(status) === expected
}

const isSceneGenerationActive = (project: any) => {
    if (!project) return false
    if (isStatus(project.status, 'generating')) return true
    const scenes = Array.isArray(project.scenes) ? project.scenes : []
    return scenes.some((scene: any) => ['pending', 'generating'].includes(normalizeProjectStatus(scene?.status)))
}

const upsertProjectListItem = (project: any) => {
    if (!project?.id) return

    const { scenes, ...summary } = project
    const index = projectList.value.findIndex((item: any) => item.id === summary.id)
    if (index === -1) {
        projectList.value = [summary, ...projectList.value]
        return
    }

    const nextList = [...projectList.value]
    nextList[index] = { ...nextList[index], ...summary }
    projectList.value = nextList
}

const syncCurrentProjectSummary = (project: any) => {
    if (!currentProject.value || !project || currentProject.value.id !== project.id) return
    currentProject.value = {
        ...currentProject.value,
        ...project,
        scenes: currentProject.value.scenes || []
    }
}

const hasBackgroundGeneratingProjects = () => {
    return projectList.value.some((project: any) => {
        if (!project || project.id === currentProject.value?.id) return false
        return normalizeProjectStatus(project.status) === 'generating'
    })
}

// Project Sidebar Data
const normalizeContextKey = (rawKey: unknown): string => {
    const text = toTextValue(rawKey).trim()
    if (!text) return ''
    return text
        .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
        .replace(/[\s-]+/g, '_')
        .toLowerCase()
        .replace(/[^a-z0-9_]/g, '')
        .replace(/_+/g, '_')
        .replace(/^_+|_+$/g, '')
}

const projectTypeLabelMap: Record<string, string> = {
    movie: '电影剧本',
    tv: '剧集剧本',
    short: '短剧剧本',
    short_video: '短视频',
    pending: '待确定'
}

const contextFieldLabelMap: Record<string, string> = {
    title: '故事题目',
    project_type: '剧本类型',
    logline: '故事梗概',
    synopsis_brief: '简要梗概',
    synopsis_detailed: '详细梗概',
    brief_synopsis: '简要梗概',
    detailed_synopsis: '详细梗概',
    story_brief: '简要梗概',
    story_detailed: '详细梗概',
    movie_duration: '电影时长',
    scene_count_target: '目标场次',
    episode_count: '集数',
    episode_duration: '单集时长',
    video_duration_seconds: '总时长（秒）',
    tone: '基调',
    time_period: '时代背景',
    story_expansion: '剧情大纲',
    character_details: '人物设定',
    plot_details: '关键设定',
    theme: '主题',
    visual_style: '视觉风格',
    user_notes: '补充说明',
    protagonist_core: '主角核心',
    antagonist_obstacle: '反派/阻碍',
    central_conflict: '核心冲突',
    target_audience: '目标受众'
}

const extractFirstNumber = (text: string) => {
    const match = text.match(/\d+/)
    return match?.[0] || ''
}

const formatProjectTypeText = (value: unknown) => {
    const raw = toTextValue(value).trim()
    if (!raw) return ''
    const normalized = normalizeContextKey(raw)
    return projectTypeLabelMap[normalized] || raw
}

const formatContextDisplayValue = (rawKey: unknown, rawValue: unknown) => {
    const key = normalizeContextKey(rawKey)
    const rawText = toTextValue(rawValue).trim()
    if (!rawText) return ''

    if (key === 'project_type') {
        return formatProjectTypeText(rawText)
    }

    if (rawText.toLowerCase() === 'pending') {
        return '待确定'
    }

    if (key === 'movie_duration' || key === 'episode_duration') {
        if (rawText.includes('分钟')) return rawText
        const n = extractFirstNumber(rawText)
        return n ? `${n} 分钟` : rawText
    }

    if (key === 'video_duration_seconds') {
        if (rawText.includes('秒')) return rawText
        const n = extractFirstNumber(rawText)
        return n ? `${n} 秒` : rawText
    }

    if (key === 'scene_count_target') {
        const n = extractFirstNumber(rawText)
        return n ? `${n} 场` : rawText
    }

    if (key === 'episode_count') {
        const n = extractFirstNumber(rawText)
        return n ? `${n} 集` : rawText
    }

    return rawText
}

const getContextFieldLabel = (rawKey: unknown) => {
    const normalized = normalizeContextKey(rawKey)
    return contextFieldLabelMap[normalized] || toTextValue(rawKey)
}

const interactionContextHiddenKeys = new Set([
    'project_type',
    'final_confirm',
    'next_step_cache',
    'synopsis_brief',
    'brief_synopsis',
    'story_brief',
    'synopsis_detailed',
    'detailed_synopsis',
    'story_detailed'
])

const interactionContextEntries = computed(() => {
    const ctx = currentProject.value?.global_context || {}
    return Object.entries(ctx)
        .filter(([rawKey]) => !toTextValue(rawKey).startsWith('_'))
        .map(([rawKey, rawValue]) => {
            const normalizedKey = normalizeContextKey(rawKey)
            return {
                rawKey: toTextValue(rawKey),
                normalizedKey,
                label: getContextFieldLabel(rawKey),
                value: formatContextDisplayValue(rawKey, rawValue)
            }
        })
        .filter(item => item.normalizedKey && !interactionContextHiddenKeys.has(item.normalizedKey) && !!item.value)
})

const progressPercentage = computed(() => {
    if (!currentProject.value || !currentProject.value.scenes || currentProject.value.scenes.length === 0) return 0
    const total = currentProject.value.scenes.length
    const completed = currentProject.value.scenes.filter((s:any) => s.status === 'completed').length
    return Math.floor((completed / total) * 100)
})

const sortedProjectList = computed(() => {
    return [...projectList.value].sort((a, b) => (Number(b?.id) || 0) - (Number(a?.id) || 0))
})

const titlePatterns = [
    /《\s*([^《》\n]{1,60}?)\s*》/,
    /〈\s*([^〈〉\n]{1,60}?)\s*〉/,
    /「\s*([^「」\n]{1,60}?)\s*」/,
    /『\s*([^『』\n]{1,60}?)\s*』/
]

const titleBreakPattern = /[，。！？：；,.!?;:\n]|--+|——|—|-/

const extractStoryTitleText = (value: unknown): string => {
    const text = toTextValue(value).trim()
    if (!text) return ''

    for (const pattern of titlePatterns) {
        const match = text.match(pattern)
        if (match?.[1]) return match[1].trim()
    }

    const shortTitle = text
        .split(titleBreakPattern, 1)[0]
        .trim()
        .replace(/^["'“”‘’《》〈〉「」『』]+|["'“”‘’《》〈〉「」『』]+$/g, '')

    if (shortTitle && shortTitle.length <= 30) return shortTitle
    return text
}

const getProjectTitle = (project: any) => {
    const contextTitle = extractStoryTitleText(project?.global_context?.title || '')
    if (contextTitle) return contextTitle

    const projectTitle = extractStoryTitleText(project?.title || '')
    if (projectTitle) return projectTitle

    return toTextValue(project?.logline || '').trim()
}

const getProjectDisplayText = (project: any) => {
    const raw = getProjectTitle(project)
    if (!raw) return 'Untitled'
    return raw.length > 24 ? `${raw.slice(0, 24)}...` : raw
}

const getProjectTooltipText = (project: any) => {
    return getProjectTitle(project)
}

const getProjectTypeDisplay = (project: any) => {
    const projectType = toTextValue(project?.project_type || '').trim()
    const contextType = toTextValue(project?.global_context?.project_type || '').trim()
    const preferredType = projectType && normalizeContextKey(projectType) !== 'pending'
        ? projectType
        : (contextType || projectType || 'pending')
    return formatProjectTypeText(preferredType) || '待确定'
}

const currentProjectTitle = computed(() => {
    return getProjectTitle(currentProject.value) || 'Untitled'
})

const currentProjectTypeDisplay = computed(() => {
    const projectType = toTextValue(currentProject.value?.project_type || '').trim()
    const contextType = toTextValue(currentProject.value?.global_context?.project_type || '').trim()

    const preferredType = projectType && normalizeContextKey(projectType) !== 'pending'
        ? projectType
        : (contextType || projectType || 'pending')

    return formatProjectTypeText(preferredType) || '待确定'
})

const isValidCharacterDetailsText = (value: unknown) => {
    const text = toTextValue(value).trim()
    if (!text) return false
    if (['经典叙事风格', '带有反转的剧情', '大胆的实验性风格'].includes(text)) return false
    if (/(叙事风格|实验风格|镜头语言)/.test(text) && !/(主角|角色|配角|反派|人物|身份|关系|秘密)/.test(text)) {
        return false
    }
    if (text.length < 12 && !/(主角|角色|配角|人物)/.test(text)) return false
    return true
}

const characterDetailsText = computed(() => {
    const raw = currentProject.value?.global_context?.character_details || ''
    return isValidCharacterDetailsText(raw) ? toTextValue(raw) : ''
})

const isControlInteractionField = (field: string) => {
    return ['final_confirm', 'project_type', 'movie_duration', 'scene_count_target', 'episode_count', 'episode_duration'].includes(field)
}

const canUseCustomInput = computed(() => {
    const field = toTextValue(interaction.value?.field || '').trim()
    if (!field) return true
    if (field === 'video_duration_seconds') return true
    return !isControlInteractionField(field)
})

const customInputPlaceholder = computed(() => {
    const field = toTextValue(interaction.value?.field || '').trim()
    if (field === 'video_duration_seconds') {
        return '输入自定义时长（秒），例如 180'
    }
    return '输入您的想法...'
})

const shouldShowOptionValue = (opt: any) => {
    const label = toTextValue(opt?.label).trim()
    const value = toTextValue(opt?.value).trim()
    if (!value || value === label) return false
    if (value.length <= 24 && /^(movie|tv|short|short_video|\d+|confirmed|reset|edit:)/.test(value)) return false
    return true
}

const storySynopsis = computed(() => {
    if (!currentProject.value) {
        return { brief: '', detailed: '' }
    }

    const context = currentProject.value.global_context || {}
    const brief = toTextValue(
        context.synopsis_brief ||
        context.brief_synopsis ||
        context.story_brief ||
        ''
    )

    let detailed = toTextValue(
        context.synopsis_detailed ||
        context.detailed_synopsis ||
        context.story_detailed ||
        context.story_expansion ||
        context.plot_details ||
        ''
    )

    if (!detailed) {
        const sceneOutlines = (currentProject.value.scenes || [])
            .map((scene: any) => scene?.outline)
            .filter(Boolean)
        if (sceneOutlines.length > 0) {
            detailed = sceneOutlines
                .map((outline: string, index: number) => `${index + 1}. ${outline}`)
                .join('\n')
        }
    }

    return { brief, detailed }
})

// --- API Client ---
const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
    if (token.value) config.headers.Authorization = `Bearer ${token.value}`
    return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      if (token.value) {
        ElMessage.error('登录状态已失效，请重新登录')
        logout()
      }
    }
    return Promise.reject(error)
  }
)

const reviewAndMaybeRewriteInput = async (rawInput: string, sourceLabel: string) => {
    const text = (rawInput || '').trim()
    if (!text) return text

    try {
        const res = await api.post('/content/review', { text })
        const review = res.data || {}
        if (!review.flagged) return text

        const categoryText = Array.isArray(review.categories) && review.categories.length > 0
            ? review.categories.join('、')
            : '不当内容'
        const suggestedText = toTextValue(
            review.suggested_rewrite || review.suggested_text || ''
        ).trim()
        const reasonText = toTextValue(review.reason || '')

        let content = `<p>检测到${escapeHtml(sourceLabel)}中可能包含“${escapeHtml(categoryText)}”。是否使用 AI 改写版本？</p>`
        if (reasonText) {
            content += `<p style="margin-top:8px;color:#6b7280;">${escapeHtml(reasonText)}</p>`
        }
        if (suggestedText) {
            content += `<div style="margin-top:12px;padding:10px;border:1px solid #dbeafe;background:#eff6ff;border-radius:8px;color:#1f2937;white-space:pre-wrap;">${escapeHtml(suggestedText)}</div>`
        } else {
            content += '<p style="margin-top:8px;color:#9ca3af;">当前未生成改写内容，点击“保留原文”可继续。</p>'
        }

        try {
            await ElMessageBox.confirm(content, '内容合规提示', {
                confirmButtonText: '使用 AI 改写',
                cancelButtonText: '保留原文',
                type: 'warning',
                dangerouslyUseHTMLString: true
            })
            if (suggestedText) {
                ElMessage.success('已应用 AI 改写版本')
                return suggestedText
            }
            return text
        } catch {
            return text
        }
    } catch (e) {
        console.error('Content review failed', e)
        return text
    }
}

// --- Logic ---
const handleAuth = async () => {
    if (!authForm.value.username || !authForm.value.password) {
        ElMessage.warning('请输入用户名和密码')
        return
    }
    authLoading.value = true
    try {
        if (isLoginMode.value) {
            const formData = new FormData()
            formData.append('username', authForm.value.username)
            formData.append('password', authForm.value.password)
            const res = await api.post('/token', formData)
            
            // Set token immediately to trigger view switch
            token.value = res.data.access_token
            localStorage.setItem('token', token.value)
            ElMessage.success('登录成功')
            
            // Fetch data in background so we don't block the UI transition
            fetchUser()
            await fetchProjects()
            startPolling()
        } else {
            await api.post('/auth/register', authForm.value)
            ElMessage.success('注册成功，请使用新账号登录')
            isLoginMode.value = true
            // Optional: Auto-fill password for convenience
            // authForm.value.password = '' 
        }
    } catch (e: any) {
        ElMessage.error(e.response?.data?.detail || "认证失败")
        console.error("Auth Error:", e)
    } finally {
        authLoading.value = false
    }
}

const fetchUser = async () => {
    if (!token.value) return
    try {
        const res = await api.get('/users/me')
        user.value = res.data
    } catch (e) {
        console.error("Fetch User Failed", e)
    }
}

const fetchProjects = async () => {
    if (!token.value) return
    try {
        const res = await api.get('/projects/')
        projectList.value = res.data
        if (currentProject.value) {
            const found = projectList.value.find((p: any) => p.id === currentProject.value.id)
            if (found) {
                syncCurrentProjectSummary(found)
            }
        }
    } catch (e: any) { 
        if (e.response && e.response.status === 401) return
        console.error(e) 
    }
}

const fetchProjectDetail = async (projectId: number) => {
    if (!token.value || !projectId) return null
    try {
        const res = await api.get(`/projects/${projectId}`)
        const project = res.data
        upsertProjectListItem(project)
        if (currentProject.value?.id === projectId) {
            currentProject.value = {
                ...(currentProject.value || {}),
                ...project
            }
        }
        return project
    } catch (e: any) {
        if (e.response?.status === 404) {
            projectList.value = projectList.value.filter((item: any) => item.id !== projectId)
            if (currentProject.value?.id === projectId) currentProject.value = null
            return null
        }
        if (e.response?.status !== 401) {
            console.error(e)
        }
        return null
    }
}

const syncProjectTokensFromResponse = (payload: any) => {
    if (!currentProject.value || !payload) return
    const nextTokens = Number(payload?.total_tokens)
    if (!Number.isFinite(nextTokens) || nextTokens < 0) return
    currentProject.value = {
        ...currentProject.value,
        total_tokens: Math.floor(nextTokens)
    }
}

const runPollingCycle = async () => {
    if (!token.value || !isDocumentVisible()) return
    if (pollRequestInFlight.value) return

    pollRequestInFlight.value = true
    try {
        if (currentProject.value?.id && isSceneGenerationActive(currentProject.value)) {
            await fetchProjectDetail(currentProject.value.id)
            return
        }
        if (hasBackgroundGeneratingProjects()) {
            await fetchProjects()
        }
    } finally {
        pollRequestInFlight.value = false
    }
}

const startPolling = () => {
    stopPolling()
    if (!token.value || !isDocumentVisible()) return

    let delay = 0
    if (currentProject.value?.id && isSceneGenerationActive(currentProject.value)) {
        delay = ACTIVE_PROJECT_POLL_INTERVAL_MS
    } else if (hasBackgroundGeneratingProjects()) {
        delay = BACKGROUND_LIST_POLL_INTERVAL_MS
    }

    if (!delay) return

    pollTimer.value = window.setTimeout(async () => {
        await runPollingCycle()
        startPolling()
    }, delay)
}

const stopPolling = () => {
    if (pollTimer.value) {
        clearTimeout(pollTimer.value)
        pollTimer.value = null
    }
}

const handleVisibilityChange = () => {
    if (!token.value) return
    if (isDocumentVisible()) {
        startPolling()
        return
    }
    stopPolling()
}

const logout = () => {
    try { stopPolling() } catch(e) { console.error(e) }
    token.value = ''
    user.value = null
    showAdmin.value = false
    localStorage.removeItem('token')
    projectList.value = []
    currentProject.value = null
    interaction.value = null
    scenePromptMap.value = {}
    scenePromptLoadingMap.value = {}
    ElMessage.info('已退出登录')
}

// Start polling if token exists on load
if (token.value) {
    fetchUser()
    fetchProjects().finally(() => startPolling())
} 

onMounted(() => {
    if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', handleVisibilityChange)
    }
})

onUnmounted(() => {
    stopPolling()
    if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
})

const createProject = async () => {
  if (!logline.value) {
      ElMessage.warning('请输入您的创意')
      return
  }
  const reviewedLogline = await reviewAndMaybeRewriteInput(logline.value, '用户输入')
  logline.value = reviewedLogline
  loading.value = true
  loadingText.value = '正在为您构建故事世界...'
  try {
    // 1. Create Project (Logline Only)
    const res = await api.post('/projects/', {
      logline: reviewedLogline,
      title: "创意草稿 " + new Date().toLocaleDateString(),
      project_type: "pending" // Explicitly mark as pending classification
    })
    currentProject.value = res.data
    scenePromptMap.value = {}
    scenePromptLoadingMap.value = {}
    logline.value = '' // Clear input
    
    // 2. Trigger analysis (which will now ask for Type first)
    await analyzeLogline(res.data.id)
    await fetchProjects()
    startPolling()
  } catch (e) {
      ElMessage.error('创建失败，请稍后重试')
      console.error(e)
  } finally {
    loading.value = false
  }
}

const analyzeLogline = async (id: number) => {
  if (!id) {
    console.error("Analysis invoked without ID")
    return
  }
  try {
    interaction.value = null // Clear previous to show loading state if needed
    loading.value = true
    loadingText.value = 'AI 正在阅读您的创意并构思问题，如遇波动系统会自动重试...'
    
    const res = await api.post(`/projects/${id}/analyze`)
    syncProjectTokensFromResponse(res.data)
    
    if (res.data.type === 'interaction_required') {
      interaction.value = res.data.payload
      // Reset inputs
      selectedOption.value = ''
      customInput.value = '' 
    } else if (res.data.type === 'completed') {
        // Setup complete. Only auto-generate for fresh projects.
        interaction.value = null
        const latestProject = await fetchProjectDetail(id)
        const activeProject = latestProject || currentProject.value
        const activeStatus = normalizeProjectStatus(activeProject?.status)
        const hasGeneratedScenes = Array.isArray(activeProject?.scenes) && activeProject.scenes.length > 0

        if (hasGeneratedScenes || ['generating', 'completed'].includes(activeStatus)) {
            return
        }

        loadingText.value = '基础设定完成！AI 正在为您生成分场大纲（这可能需要几分钟，请耐心等待）...'
        ElMessage.success('基础设定完成！正在生成分场大纲...')

        await api.post(
            `/projects/${id}/generate_scenes`,
            null,
            {
                params: { selected_option: 'auto' },
                timeout: 300000 // 5 minutes timeout for large batches
            }
        )

        await fetchProjectDetail(id)
    } else {
        interaction.value = null
        if (currentProject.value?.id) {
            await fetchProjectDetail(currentProject.value.id)
        }
    }
    } catch (e: any) { 
        console.error(e) 
        ElMessage.error(e.response?.data?.detail || '分析失败，请检查网络或后端日志')
    } finally {
      loading.value = false
      startPolling()
  }
}

const submitChoice = async () => {
    if (!currentProject.value) return
    
    let finalAnswer = selectedOption.value || customInput.value
    if (!finalAnswer) {
        ElMessage.warning('请选择一个选项或自行输入')
        return
    }

    // Only review direct user free text, not AI-provided option values.
    if (!selectedOption.value && customInput.value) {
        finalAnswer = await reviewAndMaybeRewriteInput(customInput.value, '用户输入')
        customInput.value = finalAnswer
    }

    loading.value = true
    loadingText.value = '正在记录您的决定并生成下一个问题...'
    
    try {
        // We now treat all interactions as "updating project state"
        // The backend `update_project` PATCH can handle generic context updates if we design it so.
        // But currently we have specific logic.
        
        // Strategy: 
        // 1. If it's the "Type" question (special case), we use PATCH project_type
        // 2. For all other "Questions", we send the answer to a generic endpoint or the analyze endpoint to record it.
        
        // Let's assume the question payload has a 'field' property to know what we are answering?
        // Or we just send it to `analyze` as an answer.
        
        // CURRENT BACKEND LIMITATION: It expects PATCH project_type or POST generate_scenes.
        // WE NEED TO UPDATE BACKEND to accept generic Q&A.
        
        // Temporary Hybrid:
        if (['movie', 'tv', 'short', 'short_video'].includes(finalAnswer) && !interaction.value.field) {
             // Backward compatible "Type" selection
             await api.patch(`/projects/${currentProject.value.id}`, { project_type: finalAnswer })
        } else {
             // Send answer to analyze endpoint or a new interaction endpoint
             // We'll use a new POST /projects/{id}/submit_interaction
             const res = await api.post(`/projects/${currentProject.value.id}/interact`, {
                 answer: finalAnswer,
                 context_key: interaction.value.field || 'unknown' // Backend should provide this in payload
             })
             if (res.data?.title) currentProject.value.title = res.data.title
             if (res.data?.context) currentProject.value.global_context = res.data.context
             syncProjectTokensFromResponse(res.data)
        }
        
        interaction.value = null
        // Trigger next step immediately
        await analyzeLogline(currentProject.value.id)
        
    } catch (e: any) {
        console.error(e)
        ElMessage.error(e.response?.data?.detail || '提交失败，请稍后重试')
    } finally { loading.value = false }
}

const handleOptionSelect = (opt: any) => {
    selectedOption.value = opt.value
    customInput.value = '' // clear manual input
}

const startNewProject = () => {
    currentProject.value = null
    interaction.value = null
    loading.value = false
    switchingProject.value = false
    scenePromptMap.value = {}
    scenePromptLoadingMap.value = {}
}

const loadProject = async (p: any) => {
    // Prevent accidental switch if generating
    if (currentProject.value && normalizeProjectStatus(currentProject.value.status) === 'generating' && currentProject.value.id !== p.id) {
        try {
            await ElMessageBox.confirm(
                '当前创意正在生成中，切换项目您将无法实时看到生成进度（任务会在后台继续）。确定要切换吗？',
                '确认切换',
                { confirmButtonText: '切换', cancelButtonText: '取消', type: 'warning' }
            )
        } catch {
            return
        }
    }
    
    switchingProject.value = true
    loading.value = true
    loadingText.value = '正在加载历史剧本...'

    try {
        scenePromptMap.value = {}
        scenePromptLoadingMap.value = {}
        currentProject.value = {
            ...p,
            scenes: Array.isArray(p?.scenes) ? p.scenes : []
        }
        drawerOpen.value = false
        interaction.value = null

        const detailedProject = await fetchProjectDetail(p.id)
        if (detailedProject) {
            currentProject.value = detailedProject
            upsertProjectListItem(detailedProject)
        }

        // Always check state/resume flow
        const activeProject = detailedProject || currentProject.value
        const activeStatus = normalizeProjectStatus(activeProject?.status)
        const hasScenes = Array.isArray(activeProject?.scenes) && activeProject.scenes.length > 0

        if (activeStatus === 'generating') {
            loading.value = false
        } else if (!hasScenes && activeStatus !== 'completed' && activeStatus !== 'failed') {
            loading.value = true
            loadingText.value = "正在恢复进度..."
            await analyzeLogline(activeProject.id)
        } else {
            loading.value = false
        }

        startPolling()
    } finally {
        switchingProject.value = false
    }
}

const deleteProject = async () => {
    if (!currentProject.value) return
    
    try {
        await ElMessageBox.confirm(
            '确定要删除此创意吗？生成任务将终止。',
            '提示',
            {
                confirmButtonText: '确定',
                cancelButtonText: '取消',
                type: 'warning',
            }
        )
        
        await api.delete(`/projects/${currentProject.value.id}`)
        ElMessage.success('已删除')
        currentProject.value = null
        await fetchProjects()
        startPolling()
    } catch (e) {
        if (e !== 'cancel') {
            console.error(e)
            ElMessage.error('删除失败')
        }
    }
}

const regenerateScene = async (sceneId: number, sceneIndex: number) => {
    if (!currentProject.value) return;
    try {
        await api.post(`/projects/${currentProject.value.id}/scenes/${sceneIndex}/regenerate`)
        ElMessage.success(`已请求重写第 ${sceneIndex} 场`)
        // Update local state to reflect pending
        const s = currentProject.value.scenes.find((x:any) => x.id === sceneId)
        if (s) {
            s.status = 'pending'
            s.content = ''
        }
        await fetchProjectDetail(currentProject.value.id)
        startPolling()
    } catch(e) { console.error(e); ElMessage.error('重试请求失败') }
}

const getScenePrompt = (sceneId: number) => {
    return toTextValue(scenePromptMap.value[sceneId] || '')
}

const isScenePromptLoading = (sceneId: number) => {
    return !!scenePromptLoadingMap.value[sceneId]
}

const convertSceneToPrompt = async (scene: any) => {
    if (!currentProject.value?.id || !scene?.scene_index || !scene?.id) return

    const sceneId = Number(scene.id)
    const sceneIndex = Number(scene.scene_index)
    if (!sceneId || !sceneIndex) return

    scenePromptLoadingMap.value = { ...scenePromptLoadingMap.value, [sceneId]: true }

    try {
        const res = await api.post(`/projects/${currentProject.value.id}/scenes/${sceneIndex}/to_prompt`)
        const promptText = toTextValue(res.data?.prompt).trim()
        if (!promptText) {
            ElMessage.warning('AI 提示词为空，请重试')
            return
        }
        scenePromptMap.value = {
            ...scenePromptMap.value,
            [sceneId]: promptText
        }
        ElMessage.success('已生成 AI 提示词')
    } catch (e: any) {
        console.error(e)
        ElMessage.error(e.response?.data?.detail || '转写失败，请稍后重试')
    } finally {
        scenePromptLoadingMap.value = { ...scenePromptLoadingMap.value, [sceneId]: false }
    }
}

const exportScript = (format: string = 'txt') => {
    if (!currentProject.value) return
    
    // Use backend endpoint
    const url = `/api/projects/${currentProject.value.id}/export?format=${format}`
    
    // Create hidden link to download
    const link = document.createElement('a')
    link.href = url
    link.target = '_blank'
    // Add auth token to url if needed, but usually browser handles cookies or we need to pass token in query for pure GET link download if Authorization header is not possible via simple link click.
    // Since we use Bearer token in headers for AJAX, direct link click might fail if backend requires Auth header.
    // Solution: Use axios to download blob.
    
    api.get(`/projects/${currentProject.value.id}/export?format=${format}`, { responseType: 'blob' })
       .then((response) => {
           const url = window.URL.createObjectURL(new Blob([response.data]));
           const link = document.createElement('a');
           link.href = url;
           // Try to extract filename from header
           const contentDisposition = response.headers['content-disposition'];
           let fileName = `script.${format}`;
           if (contentDisposition) {
               const fileNameMatch = contentDisposition.match(/filename=(.+)/);
               if (fileNameMatch && fileNameMatch.length === 2) fileName = fileNameMatch[1];
           }
           link.setAttribute('download', fileName);
           document.body.appendChild(link);
           link.click();
           document.body.removeChild(link);
       })
       .catch(e => ElMessage.error('导出失败'))
}

// Sorted Key Settings Help
const keySettingsOrder = [
    'title', 'theme', 'tone', 'time_period', 
    'protagonist_core', 'antagonist_obstacle', 'central_conflict',
    'visual_style', 'target_audience', 
    'episode_count', 'episode_duration', 'movie_duration', 'video_duration_seconds', 'scene_count_target',
    'plot_details', 'story_expansion', 'user_notes'
]

const sortedContext = computed(() => {
    if (!currentProject.value?.global_context) return []
    const ctx = currentProject.value.global_context
    const hiddenKeys = new Set([
        'logline',
        'character_details',
        'project_type',
        'final_confirm',
        'next_step_cache',
        'synopsis_brief',
        'brief_synopsis',
        'story_brief',
        'synopsis_detailed',
        'detailed_synopsis',
        'story_detailed'
    ])

    const entries = Object.entries(ctx)
        .filter(([rawKey]) => {
            const keyText = toTextValue(rawKey)
            if (!keyText || keyText.startsWith('_')) return false
            const normalizedKey = normalizeContextKey(rawKey)
            return !!normalizedKey && !hiddenKeys.has(normalizedKey)
        })
        .map(([rawKey, rawValue]) => {
            const normalizedKey = normalizeContextKey(rawKey)
            return {
                key: toTextValue(rawKey),
                normalizedKey,
                label: getContextFieldLabel(rawKey),
                value: formatContextDisplayValue(rawKey, rawValue)
            }
        })
        .filter(item => !!item.value)

    return entries.sort((a, b) => {
        const idxA = keySettingsOrder.indexOf(a.normalizedKey)
        const idxB = keySettingsOrder.indexOf(b.normalizedKey)
        if (idxA !== -1 && idxB !== -1) return idxA - idxB
        if (idxA !== -1) return -1
        if (idxB !== -1) return 1
        return a.label.localeCompare(b.label, 'zh-Hans-CN')
    })
})

const copyText = (value: unknown) => {
    const text = toTextValue(value)
    if (!text) return
    
    // Fallback for secure context issues or older mobiles
    const unsecuredCopyToClipboard = (val: string) => {
        const textArea = document.createElement("textarea");
        textArea.value = val;
        // Ensure not visible but part of DOM
        textArea.style.position = "absolute"; 
        textArea.style.left = "-9999px"; 
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy');
            ElMessage.success('已复制')
        } catch (err) {
            console.error('Unable to copy', err);
            ElMessage.error('复制失败，请长按手动复制')
        }
        document.body.removeChild(textArea);
    }

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => {
            ElMessage.success('已复制')
        }).catch(() => {
            unsecuredCopyToClipboard(text)
        })
    } else {
        unsecuredCopyToClipboard(text)
    }
}
</script>

<template>
  <div class="min-h-screen bg-gray-50 text-slate-700 font-sans">
    
    <!-- Auth Overlay -->
    <div v-if="!token" class="fixed inset-0 z-50 bg-white/95 flex flex-col items-center justify-center p-6">
        <div class="w-full max-w-sm">
            <div class="text-center mb-8">
                <img src="/logo.png" alt="LuminaScript" class="h-24 mx-auto mb-4" />
                <h1 class="text-3xl font-light tracking-wide text-slate-800">妙笔流光 <span class="text-base block mt-2 font-normal text-gray-400">LuminaScript</span></h1>
            </div>
            <div class="bg-white p-8 rounded-2xl shadow-xl border border-gray-100">
                <el-input v-model="authForm.username" placeholder="用户名" class="mb-4" size="large">
                    <template #prefix><el-icon><User /></el-icon></template>
                </el-input>
                <el-input v-model="authForm.password" type="password" placeholder="密码" show-password class="mb-6" size="large">
                    <template #prefix><el-icon><MagicStick /></el-icon></template>
                </el-input>
                <el-button type="primary" class="w-full !rounded-xl !h-12 !text-lg" @click="handleAuth" :loading="authLoading">
                    {{ isLoginMode ? '进入创作室' : '注册账号' }}
                </el-button>
                <div class="mt-6 text-center text-sm text-gray-500 cursor-pointer hover:underline" @click="isLoginMode = !isLoginMode">
                    {{ isLoginMode ? '新用户？去注册' : '已有账号？去登录' }}
                </div>
            </div>
        </div>
    </div>

    <!-- Main Layout -->
    <div v-else class="flex flex-col h-screen">
        
        <!-- Header -->
        <header class="bg-white border-b border-gray-200 h-16 flex items-center justify-between px-4 lg:px-8 shadow-sm shrink-0 z-20">
            <div class="flex items-center gap-3">
                <el-button :icon="IconMenu" circle class="lg:hidden" @click="drawerOpen = true" />
                <img src="/logo.png" alt="Logo" class="h-8 w-auto hidden lg:block" />
                <span class="text-xl font-light tracking-tight text-slate-800">妙笔<span class="font-bold">流光</span></span>
            </div>
            <!-- Logline Display in Header -->
            <div v-if="currentProject && currentProject.logline" class="hidden md:block flex-1 mx-8 max-w-2xl">
                 <div class="text-xs text-gray-400 font-bold uppercase tracking-wider mb-1">我的创意</div>
                 <div class="text-sm text-gray-600 truncate" :title="currentProject.logline">
                     {{ currentProject.logline }}
                 </div>
            </div>
            <div class="flex items-center gap-3">
                 <el-dropdown v-if="currentProject && currentProject.scenes && currentProject.scenes.length > 0" @command="exportScript">
                    <el-button plain>
                        <el-icon class="mr-1"><Download /></el-icon> 导出 <el-icon class="el-icon--right"><arrow-down /></el-icon>
                    </el-button>
                    <template #dropdown>
                        <el-dropdown-menu>
                            <el-dropdown-item command="txt">纯文本 (.txt)</el-dropdown-item>
                            <el-dropdown-item command="md">Markdown (.md)</el-dropdown-item>
                            <el-dropdown-item command="docx">Word 文档 (.docx)</el-dropdown-item>
                        </el-dropdown-menu>
                    </template>
                 </el-dropdown>
                 <el-button type="primary" round :icon="Plus" @click="startNewProject">开始新创意</el-button>
            </div>
        </header>

        <div class="flex flex-1 overflow-hidden">
            
            <!-- Sidebar (Desktop) -->
            <aside class="hidden lg:flex w-72 bg-white border-r border-gray-200 flex-col overflow-y-auto">
                <div class="flex-1 overflow-y-auto pt-6">
                    <!-- History List -->
                    <div class="px-4 pb-4">
                        <div class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3 px-2">历史记录</div>
                        <ul class="space-y-1">
                            <li v-for="p in sortedProjectList" :key="p.id" 
                                @click="loadProject(p)"
                                class="px-3 py-3 rounded-lg cursor-pointer transition flex items-center gap-3"
                                :class="currentProject?.id === p.id ? 'bg-blue-50 text-blue-600 font-medium' : 'text-gray-600 hover:bg-gray-50'">
                                <el-icon><Document /></el-icon>
                                <div class="min-w-0 flex-1 space-y-1">
                                    <div class="text-sm sidebar-item-text" :title="getProjectTooltipText(p)">
                                        {{ getProjectDisplayText(p) }}
                                    </div>
                                    <el-tag size="small" effect="plain" class="project-type-tag">{{ getProjectTypeDisplay(p) }}</el-tag>
                                </div>
                            </li>
                        </ul>
                        <div v-if="sortedProjectList.length === 0" class="text-center text-gray-400 text-sm py-8">
                            暂无历史
                        </div>
                    </div>
                </div>
                
                <!-- Bottom: User & Logout -->
                <div class="p-4 border-t border-gray-100 bg-gray-50/50">
                    <div class="flex items-center justify-between px-2">
                         <div class="flex items-center gap-2 text-sm text-gray-600">
                             <div class="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold">
                                 <el-icon><User /></el-icon>
                             </div>
                             <div class="flex flex-col">
                                 <span>{{ user?.username || '我的账号' }}</span>
                                 <el-button v-if="user?.is_admin" link type="primary" size="small" class="!px-0 !h-auto" @click="showAdmin = true">
                                    管理后台
                                 </el-button>
                             </div>
                         </div>
                         <el-button link class="text-gray-400 hover:text-red-500" @click="logout">
                             <el-icon class="mr-1"><SwitchButton /></el-icon> 退出
                         </el-button>
                    </div>
                </div>
            </aside>

            <!-- Sidebar (Mobile Drawer) -->
            <el-drawer v-model="drawerOpen" direction="ltr" size="80%" class="lg:hidden">
                <template #header>
                    <div class="text-lg font-bold">我的剧本</div>
                </template>
                <div class="flex flex-col h-full">
                    <!-- Mobile Logline Display -->
                    <div v-if="currentProject && currentProject.logline" class="px-4 py-3 bg-blue-50/50 border-b border-blue-100 mb-2">
                        <div class="text-xs font-bold text-blue-400 uppercase tracking-wider mb-1">我的创意 (点击复制)</div>
                        <div class="text-sm text-blue-900 leading-relaxed active:opacity-70" @click="copyText(currentProject.logline)">
                            {{ currentProject.logline }}
                        </div>
                    </div>

                    <div class="flex-1 overflow-y-auto">
                        <ul class="space-y-2 p-1">
                            <div class="p-2">
                                <el-button class="w-full" :icon="Plus" @click="startNewProject(); drawerOpen=false">新创意</el-button>
                            </div>
                            <li v-for="p in sortedProjectList" :key="p.id" 
                                @click="loadProject(p)"
                                class="p-4 rounded-lg bg-gray-50 text-gray-700 border border-gray-100 shadow-sm active:bg-blue-50">
                                <div class="sidebar-item-text text-sm" :title="getProjectTooltipText(p)">
                                    {{ getProjectDisplayText(p) }}
                                </div>
                                <div class="mt-2">
                                    <el-tag size="small" effect="plain" class="project-type-tag">{{ getProjectTypeDisplay(p) }}</el-tag>
                                </div>
                            </li>
                        </ul>
                        <div v-if="sortedProjectList.length === 0" class="text-center text-gray-400 text-sm py-8">
                            暂无历史
                        </div>
                    </div>
                    
                    <!-- Mobile Footer -->
                    <div class="p-4 border-t border-gray-100 bg-gray-50 shrink-0">
                         <div class="flex items-center justify-between px-1">
                             <div class="flex items-center gap-2 text-sm text-gray-600">
                                 <el-icon><User /></el-icon>
                                 <div class="flex flex-col">
                                     <span>{{ user?.username || '我的账号' }}</span>
                                      <el-button v-if="user?.is_admin" link type="primary" size="small" class="!px-0 !h-auto" @click="showAdmin = true; drawerOpen=false">
                                        管理后台
                                     </el-button>
                                 </div>
                             </div>
                             <el-button link type="danger" @click="logout(); drawerOpen=false">
                                 <el-icon class="mr-1"><SwitchButton /></el-icon> 退出
                             </el-button>
                        </div>
                    </div>
                </div>
            </el-drawer>

            <!-- Workspace -->
            <main class="flex-1 overflow-y-auto p-4 lg:p-12 flex flex-col items-center bg-gray-50/50">
                
                <!-- Stage 1: Input -->
                <div v-if="!currentProject" class="w-full max-w-2xl animate-fade-in-up">
                    <div class="text-center mb-10">
                        <h2 class="text-3xl font-light text-slate-800 mb-2">你的故事是什么？</h2>
                        <p class="text-gray-500">输入一个简单的灵感，AI 将为您构建完整的剧本世界。</p>
                    </div>
                    
                    <div class="bg-white p-2 rounded-2xl shadow-lg border border-gray-100 transition hover:shadow-xl">
                        <el-input
                            v-model="logline"
                            :rows="6"
                            type="textarea"
                            placeholder="例如：一位退休的刺客因为他的狗被偷而被迫重出江湖..."
                            class="!text-lg !border-none"
                            resize="none"
                        />
                         <div class="p-2 flex justify-end">
                            <el-button type="primary" size="large" circle class="!w-12 !h-12 shadow-md" @click="createProject" :loading="loading">
                                <el-icon class="text-xl"><MagicStick /></el-icon>
                            </el-button>
                        </div>
                    </div>
                </div>

                <!-- Stage 2: Interaction -->
                <div v-if="currentProject && interaction" class="w-full max-w-2xl mt-8 animate-fade-in-up flex gap-6">
                     
                     <!-- Project Context Sidebar -->
                     <div class="hidden md:block w-64 shrink-0 space-y-4">
                        <div class="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
                             <div class="text-xs font-bold text-gray-400 uppercase mb-2">当前设定</div>
                             <div class="space-y-3 text-sm">
                                 <div>
                                     <div class="text-gray-500">类型</div>
                                     <div class="font-medium truncate">{{ currentProjectTypeDisplay }}</div>
                                 </div>
                                  <div v-for="item in interactionContextEntries" :key="item.rawKey">
                                     <div class="text-gray-500">{{ item.label }}</div>
                                     <div class="font-medium line-clamp-2">{{ item.value }}</div>
                                 </div>
                             </div>
                        </div>
                     </div>

                     <div class="flex-1 bg-white rounded-2xl shadow-xl overflow-hidden border border-gray-100">
                        <div class="bg-blue-50/50 p-6 border-b border-blue-100 flex items-center justify-between">
                            <div>
                                <h3 class="text-lg font-medium text-blue-900">{{ interaction.question }}</h3>
                                <div v-if="interaction.progress" class="text-xs text-blue-600/70 mt-1 font-mono">
                                    Step {{ interaction.progress.current }} / {{ interaction.progress.total }}
                                </div>
                            </div>
                            <div v-if="loading" class="text-sm text-blue-500 flex items-center gap-2">
                                <el-icon class="is-loading"><Loading /></el-icon> {{ loadingText }}
                            </div>
                        </div>
                        <div class="p-6 relative">
                            <!-- Progress Bar -->
                            <div v-if="interaction.progress" class="w-full bg-blue-100 rounded-full h-1.5 mb-6 overflow-hidden">
                                <div class="bg-blue-500 h-1.5 rounded-full transition-all duration-500 shadow-sm" :style="{ width: (interaction.progress.current / interaction.progress.total * 100) + '%' }"></div>
                            </div>

                            <!-- Context Summary (For Final Confirmation Step) -->
                            <div v-if="interaction.context_summary" class="mb-6 p-4 bg-gray-50 rounded-xl border border-gray-100 text-sm max-h-64 overflow-y-auto prose prose-sm max-w-none text-gray-600">
                                <div class="font-bold text-gray-400 mb-2 uppercase text-xs">剧本设定汇总</div>
                                <div v-html="renderMarkdown(interaction.context_summary)"></div>
                            </div>

                            <!-- Loading Overlay for Interaction -->
                            <div v-if="loading" class="absolute inset-0 bg-white/60 z-10 flex items-center justify-center">
                                <!-- Spinner is in header, this disables clicks -->
                            </div>

                            <div class="space-y-3 mb-6">
                                <button 
                                    v-for="opt in interaction.options" 
                                    :key="opt.value"
                                    @click="handleOptionSelect(opt)"
                                    class="w-full text-left p-4 rounded-xl border-2 transition-all duration-200 flex items-center justify-between group hover:shadow-sm"
                                    :class="selectedOption === opt.value ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-100 hover:border-blue-200 hover:bg-gray-50'"
                                >
                                    <div>
                                        <div class="font-medium text-base">{{ opt.label }}</div>
                                        <div v-if="shouldShowOptionValue(opt)" class="text-sm text-gray-500 mt-1 font-light">{{ opt.value }}</div>
                                    </div>
                                    <div v-if="selectedOption === opt.value" class="w-5 h-5 bg-blue-500 rounded-full flex items-center justify-center shrink-0">
                                        <div class="w-2 h-2 bg-white rounded-full"></div>
                                    </div>
                                </button>
                            </div>
                            
                            <!-- Custom Input -->
                             <div v-if="canUseCustomInput" class="relative">
                                <div class="absolute -top-3 left-2 px-1 bg-white text-xs font-bold text-gray-400">或者自行输入</div>
                                <el-input 
                                    v-model="customInput"
                                    :placeholder="customInputPlaceholder"
                                    size="large"
                                    @input="selectedOption = ''"
                                />
                             </div>

                            <div class="mt-8">
                                <el-button type="primary" class="w-full !rounded-xl !h-12 !text-lg shadow-blue-200 shadow-lg" @click="submitChoice" :disabled="!selectedOption && !customInput" :loading="loading">
                                    下一步
                                </el-button>
                            </div>
                        </div>
                     </div>
                </div>

                <!-- Stage 3: Dashboard/Scripts -->
                <div v-if="currentProject && !interaction" class="w-full max-w-4xl mt-8 pb-20 animate-fade-in-up">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-4">
                            <h2 class="text-2xl font-light text-slate-800">{{ currentProjectTitle }}</h2>
                            <el-button size="small" circle :icon="Plus" @click="startNewProject" title="开启新创意"></el-button>
                            <el-button size="small" type="danger" circle :icon="Delete" @click="deleteProject" title="删除/终止任务"></el-button>
                        </div>
                        <div class="flex items-center gap-3">
                            <div class="hidden md:flex items-center gap-1 text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded-full">
                                <el-icon><Coin /></el-icon>
                                <span>消耗 Tokens: {{ currentProject.total_tokens || 0 }}</span>
                            </div>
                            <!-- Format Genre Label -->
                             <el-tag v-if="currentProject.genre" effect="dark" round>
                                {{ 
                                    currentProject.genre === 'movie' ? '电影剧本' : 
                                    currentProject.genre === 'short_drama' ? '现代短剧' :
                                    currentProject.genre.startsWith('style_') ? currentProject.genre.replace('style_', '') : 
                                    currentProject.genre 
                                }}
                            </el-tag>
                        </div>
                    </div>

                    <!-- Progress Bar -->
                    <div v-if="currentProject.scenes && currentProject.scenes.length > 0 && !isStatus(currentProject.status, 'completed')" class="mb-6 bg-white p-6 rounded-xl shadow-sm border border-blue-100 animate-pulse">
                         <div class="flex justify-between items-center mb-2">
                            <span class="text-sm font-bold text-blue-800 flex items-center gap-2">
                                <el-icon class="is-loading"><Loading /></el-icon>
                                正在创作剧本...
                            </span>
                            <span class="text-sm font-mono text-blue-600">{{ progressPercentage }}%</span>
                         </div>
                         <el-progress 
                            :percentage="progressPercentage" 
                            :stroke-width="12" 
                            :show-text="false" 
                            striped 
                            striped-flow 
                            color="#3b82f6"
                         />
                    </div>

                    <div class="space-y-6">
                        <!-- Project Info Tabs -->
                        <div v-if="currentProject.scenes && currentProject.scenes.length > 0" class="bg-white rounded-xl shadow-sm border border-gray-100 p-6 mb-6">
                             <el-tabs class="project-info-tabs">
                                <el-tab-pane label="剧情大纲">
                                    <div class="space-y-4">
                                        <el-collapse accordion>
                                            <el-collapse-item v-for="s in currentProject.scenes" :key="'out-'+s.id" :name="s.id">
                                                <template #title>
                                                    <div class="flex items-center gap-4 w-full px-2">
                                                        <span class="font-bold text-gray-400 shrink-0">#{{ s.scene_index }}</span>
                                                        <span class="text-sm text-gray-700 truncate mr-4">{{ s.outline }}</span>
                                                    </div>
                                                </template>
                                                <div class="px-4 py-2 text-sm text-gray-600 leading-relaxed bg-gray-50 rounded">
                                                    {{ s.outline }}
                                                </div>
                                            </el-collapse-item>
                                        </el-collapse>
                                    </div>
                                </el-tab-pane>
                                <el-tab-pane label="人物设定">
                                    <div v-if="characterDetailsText" class="p-2">
                                        <!-- Attempt to parse list format if present -->
                                        <div v-if="characterDetailsText.includes('\n-')" class="space-y-3">
                                            <div v-for="(line, idx) in characterDetailsText.split('\n')" :key="idx">
                                                <div v-if="line.trim().startsWith('-')" class="bg-gray-50 p-3 rounded-lg border border-gray-100 shadow-sm flex gap-3">
                                                    <div class="w-1 h-full bg-blue-400 rounded-full shrink-0 mt-1"></div>
                                                    <div class="text-sm text-gray-700 leading-relaxed prose prose-sm max-w-none" v-html="renderMarkdown(line.replace(/^-/, '').trim())"></div>
                                                </div>
                                                <div v-else-if="line.trim()" class="text-xs font-bold text-gray-400 uppercase mt-4 mb-1 pl-1">
                                                     {{ line.trim().replace(/:$/, '') }}
                                                </div>
                                            </div>
                                        </div>
                                        <div v-else class="text-sm text-gray-600 leading-relaxed prose prose-sm max-w-none" v-html="renderMarkdown(characterDetailsText)"></div>
                                    </div>
                                    <div v-else class="text-gray-400 text-sm text-center py-4">暂无详细人物设定</div>
                                </el-tab-pane>
                                <el-tab-pane label="故事梗概">
                                    <div class="space-y-4 p-2">
                                        <div class="bg-gray-50 rounded-lg border border-gray-100 p-4">
                                            <div class="font-bold text-gray-500 mb-2 flex items-center justify-between">
                                                <span>简要梗概</span>
                                                <el-button link size="small" :icon="Document" @click="copyText(storySynopsis.brief)"></el-button>
                                            </div>
                                            <div v-if="storySynopsis.brief" class="prose prose-sm text-sm text-gray-700 max-w-none" v-html="renderMarkdown(storySynopsis.brief)"></div>
                                            <div v-else class="text-sm text-gray-400">暂无简要梗概</div>
                                        </div>
                                        <div class="bg-gray-50 rounded-lg border border-gray-100 p-4">
                                            <div class="font-bold text-gray-500 mb-2 flex items-center justify-between">
                                                <span>详细梗概</span>
                                                <el-button link size="small" :icon="Document" @click="copyText(storySynopsis.detailed)"></el-button>
                                            </div>
                                            <div v-if="storySynopsis.detailed" class="prose prose-sm text-sm text-gray-700 max-w-none max-h-72 overflow-y-auto custom-scrollbar" v-html="renderMarkdown(storySynopsis.detailed)"></div>
                                            <div v-else class="text-sm text-gray-400">暂无详细梗概</div>
                                        </div>
                                    </div>
                                </el-tab-pane>
                                <el-tab-pane label="关键设定">
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                                        <div v-if="currentProject.global_context?.logline" class="col-span-full">
                                            <div class="font-bold text-gray-500 mb-1 flex items-center justify-between">
                                                故事梗概 (Logline)
                                                <el-button type="primary" link size="small" @click="copyText(currentProject.global_context.logline)">复制</el-button>
                                            </div>
                                            <div class="bg-gray-50 p-3 rounded border border-gray-100 whitespace-pre-wrap cursor-pointer hover:bg-gray-100 transition" @click="copyText(currentProject.global_context.logline)">
                                                {{ currentProject.global_context.logline }}
                                            </div>
                                        </div>
                                        <div v-for="item in sortedContext" :key="`${item.key}-${item.normalizedKey}`">
                                            <div class="font-bold text-gray-500 mb-1 capitalize flex items-center justify-between">
                                                <span>{{ item.label }}</span>
                                                <el-button link size="small" :icon="Document" @click="copyText(item.value)"></el-button>
                                            </div>
                                            
                                            <el-popover placement="top" :width="400" trigger="click">
                                                <template #reference>
                                                    <div class="bg-gray-50 p-2 rounded cursor-pointer hover:bg-blue-50 transition border border-transparent hover:border-blue-100 key-setting-preview-wrapper" :title="item.value">
                                                        <div class="prose prose-sm text-sm text-gray-600 max-w-none key-setting-preview" v-html="renderMarkdown(item.value)"></div>
                                                    </div>
                                                </template>
                                                <div class="p-2">
                                                    <h4 class="font-bold text-gray-700 mb-2 border-b pb-1">详细内容</h4>
                                                    <div class="prose prose-sm text-sm text-gray-600 max-h-60 overflow-y-auto custom-scrollbar" v-html="renderMarkdown(item.value)"></div>
                                                    <div class="mt-2 text-right">
                                                        <el-button size="small" type="primary" plain @click="copyText(item.value)">复制全文</el-button>
                                                    </div>
                                                </div>
                                            </el-popover>
                                        </div>
                                    </div>
                                </el-tab-pane>
                             </el-tabs>
                        </div>
                        
                        <div v-if="!currentProject.scenes || currentProject.scenes.length === 0" class="text-center py-10 text-gray-400">
                             <div v-if="switchingProject || loading || isStatus(currentProject.status, 'generating')">
                                <el-icon class="text-4xl mb-2 animate-spin"><Loading /></el-icon>
                                <p>{{ switchingProject ? '正在加载历史剧本...' : (loadingText || 'AI 正在逐场构架剧本，请稍候...') }}</p>
                                <p class="text-xs mt-2 text-gray-400">（受网络速度和模型提供商影响，生成速度无法控制，请耐心等待）</p>
                             </div>
                             <div v-else class="py-4">
                                <p class="mb-4 text-gray-500">剧本尚未生成或生成过程中断。</p>
                                <el-button v-if="currentProject?.id" type="primary" plain round @click="analyzeLogline(currentProject.id)">尝试重新分析</el-button>
                             </div>
                        </div>

                        <div v-for="scene in currentProject.scenes" :key="scene.id" 
                            class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden transition-all hover:shadow-md">
                            
                            <!-- Header -->
                            <div class="bg-gray-50 px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                                <span class="font-medium text-gray-700">第 {{ scene.scene_index }} 场</span>
                                <div class="flex items-center gap-2">
                                     <el-tag v-if="isStatus(scene.status, 'completed')" type="success" size="small" effect="plain">已完成</el-tag>
                                     <el-tag v-else-if="isStatus(scene.status, 'generating')" type="primary" size="small" effect="plain">生成中...</el-tag>
                                     <el-tag v-else type="info" size="small" effect="plain">等待中</el-tag>

                                     <el-button
                                        v-if="scene.content"
                                        size="small"
                                        link
                                        type="info"
                                        @click="copyText(scene.content)"
                                        title="一键复制本场内容"
                                     >
                                        <el-icon><Document /></el-icon> 复制内容
                                     </el-button>

                                     <el-button
                                        v-if="isStatus(scene.status, 'completed')"
                                        size="small"
                                        link
                                        type="success"
                                        :loading="isScenePromptLoading(scene.id)"
                                        @click="convertSceneToPrompt(scene)"
                                        title="转写为 AI 提示词"
                                     >
                                        <el-icon><MagicStick /></el-icon> 转写提示词
                                     </el-button>

                                     <!-- Regenerate Button -->
                                     <el-button 
                                        v-if="isStatus(scene.status, 'completed')" 
                                        size="small" 
                                        link 
                                        type="primary" 
                                        @click="regenerateScene(scene.id, scene.scene_index)"
                                        title="重新生成这一场"
                                     >
                                        <el-icon><MagicStick /></el-icon> 重写
                                     </el-button>
                                </div>
                            </div>
                            
                            <!-- Content -->
                            <div class="p-6">
                                <!-- Hide outline if content is generated to avoid redundancy, user can check tabs for full outline -->
                                <p v-if="!scene.content" class="text-sm text-gray-500 mb-4 bg-yellow-50 p-2 rounded border border-yellow-100">
                                    <span class="font-bold">本场目标：</span> {{ scene.outline }}
                                </p>
                                <div v-if="scene.content" class="whitespace-pre-wrap font-serif leading-relaxed text-slate-800">
                                    {{ scene.content }}
                                </div>
                                <div v-else class="h-20 flex items-center justify-center text-gray-300 italic">
                                    等待 AI 撰写...
                                </div>

                                <div v-if="getScenePrompt(scene.id)" class="mt-5 bg-blue-50 border border-blue-100 rounded-lg p-4">
                                    <div class="flex items-center justify-between mb-2">
                                        <div class="text-sm font-bold text-blue-700">AI 提示词</div>
                                        <el-button size="small" type="primary" plain @click="copyText(getScenePrompt(scene.id))">一键复制</el-button>
                                    </div>
                                    <div class="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">{{ getScenePrompt(scene.id) }}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </main>

        </div>
    </div>
    
    <AdminDashboard v-if="showAdmin" :token="token" @close="showAdmin = false" />
  </div>
</template>

<style>
/* Custom overrides for Element Plus to match "Light & Elegant" */
.el-textarea__inner {
    border: none !important;
    box-shadow: none !important;
    padding: 1.5rem !important;
    background: transparent !important;
}
.el-input__wrapper {
     border-radius: 0.75rem !important;
     box-shadow: none !important;
     background-color: #f8fafc !important;
     padding: 4px 12px;
}
.el-input__wrapper.is-focus {
    background-color: #fff !important;
    box-shadow: 0 0 0 1px #409eff !important;
}
@keyframes fade-in-up {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in-up {
    animation: fade-in-up 0.6s ease-out forwards;
}
.sidebar-item-text {
    display: -webkit-box;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-all;
}
.project-type-tag {
    max-width: 100%;
}
.key-setting-preview-wrapper {
    max-height: 4.75rem;
    overflow: hidden;
}
.key-setting-preview {
    max-height: 4rem;
    overflow: hidden;
}
.key-setting-preview p,
.key-setting-preview ul,
.key-setting-preview ol {
    margin: 0 !important;
}

.project-info-tabs .el-tabs__header {
    margin-bottom: 1rem;
}

@media (max-width: 768px) {
    .project-info-tabs .el-tabs__nav-prev,
    .project-info-tabs .el-tabs__nav-next {
        display: none !important;
    }

    .project-info-tabs .el-tabs__nav-wrap.is-scrollable {
        padding: 0 !important;
    }

    .project-info-tabs .el-tabs__nav-scroll {
        overflow-x: auto !important;
        overflow-y: hidden;
        -webkit-overflow-scrolling: touch;
        touch-action: pan-x;
        scrollbar-width: none;
        cursor: grab;
    }

    .project-info-tabs .el-tabs__nav-scroll::-webkit-scrollbar {
        display: none;
    }

    .project-info-tabs .el-tabs__nav {
        float: none;
        white-space: nowrap;
    }

    .project-info-tabs .el-tabs__item {
        padding: 0 14px !important;
    }
}
</style>
