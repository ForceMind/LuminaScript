<script setup lang="ts">
import { ref, computed, onUnmounted, nextTick } from 'vue'
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
  SwitchButton
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

// --- State ---
const token = ref(localStorage.getItem('token') || '')
const user = ref<any>(null)
const drawerOpen = ref(false)

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
const projectList = ref<any[]>([])
const pollTimer = ref<any>(null)
const isStarted = ref(false)

// Project Sidebar Data
const projectContext = computed(() => currentProject.value?.global_context || {})

const progressPercentage = computed(() => {
    if (!currentProject.value || !currentProject.value.scenes || currentProject.value.scenes.length === 0) return 0
    const total = currentProject.value.scenes.length
    const completed = currentProject.value.scenes.filter((s:any) => s.status === 'completed').length
    return Math.floor((completed / total) * 100)
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
            fetchProjects()
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

const logout = () => {
    stopPolling()
    token.value = ''
    localStorage.removeItem('token')
    projectList.value = []
    currentProject.value = null
    interaction.value = null
    ElMessage.info('已退出登录')
}

const fetchProjects = async () => {
    if (!token.value) return
    try {
        const res = await api.get('/projects/')
        projectList.value = res.data
        // Update current project view if active
        if (currentProject.value) {
             const found = projectList.value.find(p => p.id === currentProject.value.id)
             if (found) currentProject.value = found
        }
    } catch (e: any) { 
        if (e.response && e.response.status === 401) return
        console.error(e) 
    }
}

const startPolling = () => {
    stopPolling()
    pollTimer.value = setInterval(fetchProjects, 3000)
}

const stopPolling = () => {
    if (pollTimer.value) {
        clearInterval(pollTimer.value)
        pollTimer.value = null
    }
}

// Start polling if token exists on load
if (token.value) {
    fetchProjects()
    startPolling()
} 

onUnmounted(() => stopPolling())

const createProject = async () => {
  if (!logline.value) {
      ElMessage.warning('请输入您的创意')
      return
  }
  loading.value = true
  loadingText.value = '正在为您构建故事世界...'
  try {
    // 1. Create Project (Logline Only)
    const res = await api.post('/projects/', {
      logline: logline.value,
      title: "创意草稿 " + new Date().toLocaleDateString(),
      project_type: "pending" // Explicitly mark as pending classification
    })
    currentProject.value = res.data
    logline.value = '' // Clear input
    
    // 2. Trigger analysis (which will now ask for Type first)
    await analyzeLogline(res.data.id)
    await fetchProjects()
  } catch (e) {
      ElMessage.error('创建失败，请稍后重试')
      console.error(e)
  } finally {
    loading.value = false
  }
}

const analyzeLogline = async (id: number) => {
  try {
    interaction.value = null // Clear previous to show loading state if needed
    loading.value = true
    loadingText.value = 'AI 正在阅读您的创意并构思问题...'
    
    const res = await api.post(`/projects/${id}/analyze`)
    
    if (res.data.type === 'interaction_required') {
      interaction.value = res.data.payload
      // Reset inputs
      selectedOption.value = ''
      customInput.value = '' 
    } else if (res.data.type === 'completed') {
        // Analysis complete. Trigger Scene Generation automatically.
        interaction.value = null
        loadingText.value = '基础设定完成！AI 正在为您生成分场大纲...'
        ElMessage.success('基础设定完成！正在生成分场大纲...')
        
        // Call generate_scenes without selected_option, forcing it to use Context
        await api.post(`/projects/${id}/generate_scenes`, null, { params: { selected_option: 'auto' } })
        
        await fetchProjects()
    } else {
        interaction.value = null
        if (currentProject.value) {
            fetchProjects()
        }
    }
  } catch (e) { console.error(e) } finally {
      loading.value = false
  }
}

const submitChoice = async () => {
    if (!currentProject.value) return
    
    const finalAnswer = selectedOption.value || customInput.value
    if (!finalAnswer) {
        ElMessage.warning('请选择一个选项或自行输入')
        return
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
        if (['movie', 'tv', 'short'].includes(finalAnswer) && !interaction.value.field) {
             // Backward compatible "Type" selection
             await api.patch(`/projects/${currentProject.value.id}`, { project_type: finalAnswer })
        } else {
             // Send answer to analyze endpoint or a new interaction endpoint
             // We'll use a new POST /projects/{id}/submit_interaction
             await api.post(`/projects/${currentProject.value.id}/interact`, {
                 answer: finalAnswer,
                 context_key: interaction.value.field || 'unknown' // Backend should provide this in payload
             })
        }
        
        interaction.value = null
        // Trigger next step immediately
        await analyzeLogline(currentProject.value.id)
        
    } catch (e) { console.error(e) } finally { loading.value = false }
}

const handleOptionSelect = (opt: any) => {
    selectedOption.value = opt.value
    customInput.value = '' // clear manual input
}

const loadProject = (p: any) => {
    currentProject.value = p
    drawerOpen.value = false 
    // Always check state/resume flow
    if (p.status !== 'completed' && p.status !== 'failed') {
        loading.value = true;
        loadingText.value = "正在恢复进度...";
        analyzeLogline(p.id)
    }
    if (p.status === 'pending' || !p.scenes || p.scenes.length === 0) {
        // Additional checks if needed
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
    } catch (e) {
        if (e !== 'cancel') {
            console.error(e)
            ElMessage.error('删除失败')
        }
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
            <div class="flex items-center gap-3">
                 <el-button type="primary" round :icon="Plus" @click="currentProject=null">开始新创意</el-button>
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
                            <li v-for="p in projectList" :key="p.id" 
                                @click="loadProject(p)"
                                class="px-3 py-3 rounded-lg cursor-pointer transition flex items-center gap-3"
                                :class="currentProject?.id === p.id ? 'bg-blue-50 text-blue-600 font-medium' : 'text-gray-600 hover:bg-gray-50'">
                                <el-icon><Document /></el-icon>
                                <span class="truncate text-sm" :title="p.logline">{{ p.title || '无标题创意' }}</span>
                            </li>
                        </ul>
                        <div v-if="projectList.length === 0" class="text-center text-gray-400 text-sm py-8">
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
                             <span>我的账号</span>
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
                <div class="h-full overflow-y-auto pb-20">
                    <ul class="space-y-2 p-1">
                        <div class="p-2">
                            <el-button class="w-full" :icon="Plus" @click="currentProject=null; drawerOpen=false">新创意</el-button>
                        </div>
                        <li v-for="p in projectList" :key="p.id" 
                            @click="loadProject(p)"
                            class="p-4 rounded-lg bg-gray-50 text-gray-700 border border-gray-100 truncate shadow-sm active:bg-blue-50">
                            {{ p.logline }}
                        </li>
                    </ul>
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
                                     <div class="font-medium truncate">{{ currentProject.project_type || '未定' }}</div>
                                 </div>
                                  <div v-for="(value, key) in projectContext" :key="key">
                                     <div class="text-gray-500 capitalize">{{ key }}</div>
                                     <div class="font-medium line-clamp-2">{{ value }}</div>
                                 </div>
                             </div>
                        </div>
                     </div>

                     <div class="flex-1 bg-white rounded-2xl shadow-xl overflow-hidden border border-gray-100">
                        <div class="bg-blue-50/50 p-6 border-b border-blue-100 flex items-center justify-between">
                            <h3 class="text-lg font-medium text-blue-900">{{ interaction.question }}</h3>
                            <div v-if="loading" class="text-sm text-blue-500 flex items-center gap-2">
                                <el-icon class="is-loading"><Loading /></el-icon> {{ loadingText }}
                            </div>
                        </div>
                        <div class="p-6 relative">
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
                                        <div v-if="opt.value && opt.value !== opt.label" class="text-sm text-gray-500 mt-1 font-light">{{ opt.value }}</div>
                                    </div>
                                    <div v-if="selectedOption === opt.value" class="w-5 h-5 bg-blue-500 rounded-full flex items-center justify-center shrink-0">
                                        <div class="w-2 h-2 bg-white rounded-full"></div>
                                    </div>
                                </button>
                            </div>
                            
                            <!-- Custom Input -->
                             <div class="relative">
                                <div class="absolute -top-3 left-2 px-1 bg-white text-xs font-bold text-gray-400">或者自行输入</div>
                                <el-input 
                                    v-model="customInput"
                                    placeholder="输入您的想法..." 
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
                            <h2 class="text-2xl font-light text-slate-800">{{ currentProject.title }}</h2>
                            <el-button size="small" circle :icon="Plus" @click="currentProject=null" title="开启新创意"></el-button>
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
                    <div v-if="currentProject.scenes && currentProject.scenes.length > 0 && currentProject.status !== 'completed'" class="mb-6 bg-white p-6 rounded-xl shadow-sm border border-blue-100 animate-pulse">
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
                        <div v-if="!currentProject.scenes || currentProject.scenes.length === 0" class="text-center py-10 text-gray-400">
                             <div v-if="loading">
                                <el-icon class="text-4xl mb-2 animate-spin"><Loading /></el-icon>
                                <p>{{ loadingText || '正在初始化剧本结构...' }}</p>
                             </div>
                             <div v-else class="py-4">
                                <p class="mb-4 text-gray-500">剧本尚未生成或生成过程中断。</p>
                                <el-button type="primary" plain round @click="analyzeLogline(currentProject.id)">尝试重新分析</el-button>
                             </div>
                        </div>

                        <div v-for="scene in currentProject.scenes" :key="scene.id" 
                            class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden transition-all hover:shadow-md">
                            
                            <!-- Header -->
                            <div class="bg-gray-50 px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                                <span class="font-medium text-gray-700">第 {{ scene.scene_index }} 场</span>
                                <div class="flex items-center gap-2">
                                     <el-tag v-if="scene.status === 'completed'" type="success" size="small" effect="plain">已完成</el-tag>
                                     <el-tag v-else-if="scene.status === 'generating'" type="primary" size="small" effect="plain">生成中...</el-tag>
                                     <el-tag v-else type="info" size="small" effect="plain">等待中</el-tag>
                                </div>
                            </div>
                            
                            <!-- Content -->
                            <div class="p-6">
                                <p class="text-sm text-gray-500 mb-4 bg-yellow-50 p-2 rounded border border-yellow-100">
                                    <span class="font-bold">梗概：</span> {{ scene.outline }}
                                </p>
                                <div v-if="scene.content" class="whitespace-pre-wrap font-serif leading-relaxed text-slate-800">
                                    {{ scene.content }}
                                </div>
                                <div v-else class="h-20 flex items-center justify-center text-gray-300 italic">
                                    等待 AI 撰写...
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </main>

        </div>
    </div>
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
</style>
