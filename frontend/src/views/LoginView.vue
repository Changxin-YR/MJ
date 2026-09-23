<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Clapperboard } from 'lucide-vue-next'
import { useAuth } from '../stores/auth'
const router = useRouter()
const auth = useAuth()
const registerMode = ref(false)
const email = ref('')
const password = ref('')
const displayName = ref('')
const pending = ref(false)
const error = ref('')
async function submit() {
  pending.value = true; error.value = ''
  try {
    if (registerMode.value) await auth.register(email.value, password.value, displayName.value)
    else await auth.login(email.value, password.value)
    await router.push('/')
  } catch (cause) { error.value = (cause as Error).message }
  finally { pending.value = false }
}
</script>
<template>
  <main class="auth-page">
    <section class="auth-art">
      <div class="brand"><span class="brand-mark"><Clapperboard :size="19" /></span>FrameForge AI</div>
      <div class="auth-message"><h1>让故事成为<br>可制作的镜头。</h1><p>从原作、角色和分镜，到生成、审核与成片。每一步都有明确的状态、权限与来源。</p></div>
      <small>FrameForge Studio · V1</small>
    </section>
    <section class="auth-form-wrap">
      <form class="auth-form" @submit.prevent="submit">
        <h2>{{ registerMode ? '创建账户' : '欢迎回来' }}</h2>
        <p class="muted" style="font-size:14px;margin-bottom:28px">{{ registerMode ? '开始你的第一个漫剧项目。' : '登录并继续你的制作工作。' }}</p>
        <div v-if="registerMode" class="field"><label for="display">显示名称</label><input id="display" v-model="displayName" required autocomplete="name" placeholder="你的名字" /></div>
        <div class="field"><label for="email">电子邮箱</label><input id="email" v-model="email" type="email" required autocomplete="email" placeholder="name@example.com" /></div>
        <div class="field"><label for="password">密码</label><input id="password" v-model="password" type="password" required minlength="10" :autocomplete="registerMode ? 'new-password' : 'current-password'" placeholder="至少 10 位" /></div>
        <p v-if="error" class="error">{{ error }}</p>
        <button class="btn btn-primary" :disabled="pending">{{ pending ? '请稍候…' : registerMode ? '创建账户' : '登录' }}</button>
        <button type="button" class="btn btn-ghost" style="margin-top:11px" @click="registerMode = !registerMode; error = ''">{{ registerMode ? '已有账户？返回登录' : '没有账户？立即注册' }}</button>
      </form>
    </section>
  </main>
</template>
