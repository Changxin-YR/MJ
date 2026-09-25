import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './stores/auth'
import LoginView from './views/LoginView.vue'
import DashboardView from './views/DashboardView.vue'
import ProjectView from './views/ProjectView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView },
    { path: '/', component: DashboardView },
    { path: '/projects/:projectId/:section?', component: ProjectView },
  ],
})
router.beforeEach(async to => {
  const auth = useAuth()
  await auth.restore()
  if (to.path !== '/login' && !auth.user) return '/login'
  if (to.path === '/login' && auth.user) return '/'
})
export default router
