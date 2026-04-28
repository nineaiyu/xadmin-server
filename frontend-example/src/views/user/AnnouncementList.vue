<template>
  <div class="announcement-user-container">
    <el-card class="header-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><Bell /></el-icon> 系统公告</span>
        </div>
      </template>
      <el-form :inline="true" :model="searchForm" class="search-form">
        <el-form-item>
          <el-input
            v-model="searchForm.title"
            placeholder="请输入标题搜索"
            prefix-icon="Search"
            clearable
            @keyup.enter="handleSearch"
            style="width: 300px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">
            <el-icon><Search /></el-icon>
            搜索
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <div class="announcement-list" v-loading="loading">
      <div
        v-for="item in listData"
        :key="item.pk"
        class="announcement-item"
        @click="handleViewDetail(item)"
      >
        <div class="item-header">
          <div class="item-title">
            <el-tag v-if="item.is_top" type="danger" effect="dark" class="top-tag">
              置顶
            </el-tag>
            <span class="title-text">{{ item.title }}</span>
          </div>
          <div class="item-meta">
            <span class="meta-item">
              <el-icon><User /></el-icon>
              {{ item.creator_name || '系统' }}
            </span>
            <span class="meta-item">
              <el-icon><Clock /></el-icon>
              {{ formatDate(item.publish_time || item.created_time) }}
            </span>
          </div>
        </div>
        <div class="item-content">
          {{ item.content.length > 150 ? item.content.substring(0, 150) + '...' : item.content }}
        </div>
        <div class="item-footer">
          <span class="view-more">查看详情 <el-icon><Right /></el-icon></span>
        </div>
      </div>

      <el-empty v-if="listData.length === 0 && !loading" description="暂无公告" />
    </div>

    <div class="pagination-container" v-if="listData.length > 0">
      <el-pagination
        v-model:current-page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :page-sizes="[10, 20, 50]"
        layout="total, prev, pager, next"
        :total="pagination.total"
        @current-change="handleCurrentChange"
        @size-change="handleSizeChange"
      />
    </div>

    <el-dialog
      v-model="detailVisible"
      title="公告详情"
      width="700px"
      :close-on-click-modal="false"
    >
      <div class="detail-content" v-if="detailData">
        <div class="detail-header">
          <h2 class="detail-title">
            <el-tag v-if="detailData.is_top" type="danger" effect="dark" class="top-tag">
              置顶
            </el-tag>
            {{ detailData.title }}
          </h2>
          <div class="detail-meta">
            <span>
              <el-icon><User /></el-icon>
              发布人：{{ detailData.creator_name || '系统' }}
            </span>
            <span>
              <el-icon><Clock /></el-icon>
              发布时间：{{ formatDate(detailData.publish_time || detailData.created_time) }}
            </span>
          </div>
        </div>
        <el-divider />
        <div class="detail-body">
          {{ detailData.content }}
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  getUserAnnouncementList,
  getUserAnnouncementDetail,
  type UserAnnouncementItem
} from '@/api/announcement'

const router = useRouter()

const loading = ref(false)
const listData = ref<UserAnnouncementItem[]>([])
const detailVisible = ref(false)
const detailData = ref<UserAnnouncementItem | null>(null)

const searchForm = reactive({
  title: ''
})

const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0
})

const formatDate = (date: string) => {
  if (!date) return '-'
  return new Date(date).toLocaleString('zh-CN')
}

const fetchData = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: pagination.page,
      page_size: pagination.pageSize
    }
    if (searchForm.title) {
      params.title = searchForm.title
    }

    const res = await getUserAnnouncementList(params)
    listData.value = res.data.results || res.data || []
    pagination.total = res.data.count || listData.value.length
  } catch (error) {
    console.error('获取公告列表失败:', error)
    ElMessage.error('获取公告列表失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  pagination.page = 1
  fetchData()
}

const handleCurrentChange = (page: number) => {
  pagination.page = page
  fetchData()
}

const handleSizeChange = (size: number) => {
  pagination.pageSize = size
  pagination.page = 1
  fetchData()
}

const handleViewDetail = async (item: UserAnnouncementItem) => {
  try {
    const res = await getUserAnnouncementDetail(item.pk)
    detailData.value = res.data
    detailVisible.value = true
  } catch (error) {
    ElMessage.error('获取公告详情失败')
  }
}

onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.announcement-user-container {
  padding: 20px;
  max-width: 900px;
  margin: 0 auto;
}

.header-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  font-size: 18px;
  font-weight: bold;
}

.card-header .el-icon {
  margin-right: 8px;
  color: #409eff;
}

.search-form {
  margin-top: 10px;
}

.announcement-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.announcement-item {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  transition: all 0.3s ease;
  border: 1px solid #ebeef5;
}

.announcement-item:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  transform: translateY(-2px);
}

.item-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.item-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.top-tag {
  flex-shrink: 0;
}

.title-text {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-meta {
  display: flex;
  gap: 20px;
  flex-shrink: 0;
  margin-left: 20px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #909399;
  font-size: 13px;
}

.item-content {
  color: #606266;
  font-size: 14px;
  line-height: 1.8;
  margin-bottom: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.item-footer {
  display: flex;
  justify-content: flex-end;
}

.view-more {
  color: #409eff;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.pagination-container {
  margin-top: 20px;
  display: flex;
  justify-content: center;
}

.detail-content {
  padding: 10px 0;
}

.detail-header {
  text-align: center;
  margin-bottom: 20px;
}

.detail-title {
  font-size: 22px;
  font-weight: bold;
  color: #303133;
  margin: 0 0 16px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
}

.detail-meta {
  display: flex;
  justify-content: center;
  gap: 30px;
  color: #909399;
  font-size: 14px;
}

.detail-meta span {
  display: flex;
  align-items: center;
  gap: 4px;
}

.detail-body {
  color: #303133;
  font-size: 15px;
  line-height: 2;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
