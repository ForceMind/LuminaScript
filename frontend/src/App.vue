<script setup lang="ts">
import { ref } from 'vue'
import axios from 'axios'

// --- State ---
const logline = ref('')
const currentProject = ref<any>(null)
const interaction = ref<any>(null) // Stores { question, options }
const selectedOption = ref('')
const loading = ref(false)

// --- API Client ---
const api = axios.create({
  baseURL: '/api' 
})

// --- Actions ---
const createProject = async () => {
  if (!logline.value) return
  loading.value = true
  try {
    const res = await api.post('/projects/', {
      logline: logline.value,
      title: "New Script" 
    })
    currentProject.value = res.data
    // Automatically trigger analysis
    await analyzeLogline(res.data.id)
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const analyzeLogline = async (id: number) => {
  try {
    const res = await api.post(`/projects/${id}/analyze`)
    if (res.data.type === 'interaction_required') {
      interaction.value = res.data.payload
    }
  } catch (e) {
    console.error(e)
  }
}

const submitChoice = async () => {
    if (!currentProject.value || !selectedOption.value) return
    loading.value = true
    try {
        await api.post(`/projects/${currentProject.value.id}/generate_scenes`, null, {
            params: { selected_option: selectedOption.value }
        })
        interaction.value = null // Clear interaction
        alert("Generation Loop Started! (Check Backend Console)")
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}
</script>

<template>
  <div class="min-h-screen flex flex-col items-center justify-center p-8">
    <h1 class="text-4xl font-bold mb-8 text-transparent bg-clip-text bg-gradient-to-r from-red-500 to-purple-600">
      妙笔流光 LuminaScript
    </h1>

    <!-- Phase 1: Input -->
    <div v-if="!currentProject" class="w-full max-w-2xl space-y-4">
      <el-input
        v-model="logline"
        :rows="4"
        type="textarea"
        placeholder="Enter your logline (e.g. A delivery driver saves the billionaire's daughter...)"
        class="text-lg"
      />
      <el-button type="primary" size="large" @click="createProject" :loading="loading" class="w-full">
        Start Drafting
      </el-button>
    </div>

    <!-- Phase 2: Interaction Modal/Card -->
    <div v-if="interaction" class="w-full max-w-2xl">
      <el-card class="box-card">
        <template #header>
          <div class="card-header">
            <span>AI Consultant</span>
          </div>
        </template>
        <div class="mb-4 text-xl">{{ interaction.question }}</div>
        
        <el-radio-group v-model="selectedOption" class="flex flex-col space-y-4 w-full">
            <el-radio 
                v-for="opt in interaction.options" 
                :key="opt.value" 
                :label="opt.value" 
                size="large" 
                border>
                {{ opt.label }}
            </el-radio>
        </el-radio-group>

        <div class="mt-6 flex justify-end">
            <el-button type="success" size="large" @click="submitChoice" :disabled="!selectedOption" :loading="loading">
                Confirm & Generate Outline
            </el-button>
        </div>
      </el-card>
    </div>
    
  </div>
</template>

<style>
.el-card {
    background-color: #1e1e1e;
    border: 1px solid #333;
    color: #fff;
}
.el-radio.is-bordered.is-checked {
    border-color: #e50914;
}
.el-radio__input.is-checked .el-radio__inner {
    border-color: #e50914;
    background: #e50914;
}
.el-radio__label {
    color: #fff;
}
</style>
