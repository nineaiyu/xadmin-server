import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'

const routes = [
  {
    path: '/',
    redirect: '/admin/announcement'
  },
  {
    path: '/admin/announcement',
    name: 'AnnouncementList',
    component: () => import('./views/admin/AnnouncementList.vue'),
    meta: { title: '公告管理' }
  },
  {
    path: '/admin/announcement/create',
    name: 'AnnouncementCreate',
    component: () => import('./views/admin/AnnouncementForm.vue'),
    meta: { title: '新建公告' }
  },
  {
    path: '/admin/announcement/edit/:id',
    name: 'AnnouncementEdit',
    component: () => import('./views/admin/AnnouncementForm.vue'),
    meta: { title: '编辑公告' }
  },
  {
    path: '/user/announcement',
    name: 'UserAnnouncementList',
    component: () => import('./views/user/AnnouncementList.vue'),
    meta: { title: '公告列表' }
  },
  {
    path: '/user/announcement/:id',
    name: 'UserAnnouncementDetail',
    component: () => import('./views/user/AnnouncementDetail.vue'),
    meta: { title: '公告详情' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

const app = createApp(App)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
