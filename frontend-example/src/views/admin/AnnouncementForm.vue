<template>
  <div class="announcement-container">
    <el-card class="search-card">
      <el-form :inline="true" :model="searchForm" class="search-form">
        <el-form-item label="标题">
          <el-input v-model="searchForm.title" placeholder="请输入标题" clearable @keyup.enter="handleSearch" />
        </el-form-item>
        <el-form-item label="是否置顶">
          <el-select v-model="searchForm.is_top" placeholder="请选择" clearable>
            <el-option label="是" :value="true" />
            <el-option label="否" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="发布状态">
          <el-select v-model="searchForm.is_published" placeholder="请选择" clearable>
            <el-option label="已发布" :value="true" />
            <el-option label="未发布" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">
            <el-icon><Search /></el-icon>
            搜索
          </el-button>
          <el-button @click="handleReset">
            <el-icon><Refresh /></el-icon>
            重置
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="table-card">
      <template #header>
        <div class="card-header">
          <span>公告列表</span>
          <el-button type="primary" @click="handleCreate">
            <el-icon><Plus /></el-icon>
            新建公告
          </el-button>
        </div>
      </template>

      <el-table :data="tableData" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="pk" label="ID" width="100" />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="is_top" label="是否置顶" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_top ? 'success' : 'info'">
              {{ row.is_top ? '置顶' : '普通' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_published" label="发布状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_published ? 'success' : 'warning'">
              {{ row.is_published ? '已发布' : '未发布' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="creator" label="发布人" width="120">
          <template #default="{ row }">
            {{ row.creator?.username || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="publish_time" label="发布时间" width="180">
          <template #default="{ row }">
            {{ row.publish_time ? formatDate(row.publish_time) : '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="created_time" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_time) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="handleEdit(row)">
              <el-icon><Edit /></el-icon>
              编辑
            </el-button>
            <el-button type="primary" link @click="handleView(row)">
              <el-icon><View /></el-icon>
              详情
            </el-button>
            <el-button v-if="!row.is_published" type="success" link @click="handlePublish(row)">
              <el-icon><Promotion /></el-icon>
              发布
            </el-button>
            <el-button v-else type="warning" link @click="handleUnpublish(row)">
              <el-icon><Download /></el-icon>
              下架
            </el-button>
            <el-button type="danger" link @click="handleDelete(row)">
              <el-icon><Delete /></el-icon>
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-container">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          :total="pagination.total"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>

    <el-dialog v-model="detailVisible" title="公告详情" width="700px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="标题" :span="2">
          {{ detailData.title }}
        </el-descriptions-item>
        <el-descriptions-item label="是否置顶">
          <el-tag :type="detailData.is_top ? 'success' : 'info'">
            {{ detailData.is_top ? '置顶' : '普通' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="发布状态">
          <el-tag :type="detailData.is_published ? 'success' : 'warning'">
            {{ detailData.is_published ? '已发布' : '未发布' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="发布人">
          {{ detailData.creator?.username || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="发布时间">
          {{ detailData.publish_time ? formatDate(detailData.publish_time) : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">
          {{ formatDate(detailData.created_time) }}
        </el-descriptions-item>
        <el-descriptions-item label="更新时间">
          {{ formatDate(detailData.updated_time) }}
        </el-descriptions-item>
        <el-descriptions-item label="内容" :span="2">
          <div class="detail-content">{{ detailData.content }}</div>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <el-dialog v-model="deleteVisible" title="确认删除" width="400px">
      <span>确定要删除公告「{{ deleteData.title }}」吗？此操作不可恢复。</span>
      <template #footer>
        <el-button @click="deleteVisible = false">取消</el-button>
        <el-button type="danger" @click="confirmDelete">确认删除</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getAnnouncementList,
  getAnnouncementDetail,
  deleteAnnouncement,
  publishAnnouncement,
  unpublishAnnouncement,
  type AnnouncementItem
} from '@/api/announcement'

const router = useRouter()

const loading = ref(false)
const tableData = ref<AnnouncementItem[]>([])
const detailVisible = ref(false)
const deleteVisible = ref(false)
const detailData = ref<AnnouncementItem>({} as AnnouncementItem)
const deleteData = ref<AnnouncementItem>({} as AnnouncementItem)

const searchForm = reactive({
  title: '',
  is_top: undefined as boolean | undefined,
  is_published: undefined as boolean | undefined
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
    if (searchForm.title) params.title = searchForm.title
    if (searchForm.is_top !== undefined) params.is_top = searchForm.is_top
    if (searchForm.is_published !== undefined) params.is_published = searchForm.is_published

    const res = await getAnnouncementList(params)
    tableData.value = res.data.results || res.data || []
    pagination.total = res.data.count || tableData.value.length
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

const handleReset = () => {
  searchForm.title = ''
  searchForm.is_top = undefined
  searchForm.is_published = undefined
  handleSearch()
}

const handleSizeChange = (size: number) => {
  pagination.pageSize = size
  fetchData()
}

const handleCurrentChange = (page: number) => {
  pagination.page = page
  fetchData()
}

const handleCreate = () => {
  router.push('/admin/announcement/create')
}

const handleEdit = (row: AnnouncementItem) => {
  router.push(`/admin/announcement/edit/${row.pk}`)
}

const handleView = async (row: AnnouncementItem) => {
  try {
    const res = await getAnnouncementDetail(row.pk)
    detailData.value = res.data
    detailVisible.value = true
  } catch (error) {
    ElMessage.error('获取公告详情失败')
  }
}

const handlePublish = async (row: AnnouncementItem) => {
  try {
    await ElMessageBox.confirm(`确定要发布公告「${row.title}」吗？`, '确认发布', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await publishAnnouncement(row.pk)
    ElMessage.success('发布成功')
    fetchData()
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error('发布失败')
    }
  }
}

const handleUnpublish = async (row: AnnouncementItem) => {
  try {
    await ElMessageBox.confirm(`确定要下架公告「${row.title}」吗？`, '确认下架', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await unpublishAnnouncement(row.pk)
    ElMessage.success('下架成功')
    fetchData()
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error('下架失败')
    }
  }
}

const handleDelete = (row: AnnouncementItem) => {
  deleteData.value = row
  deleteVisible.value = true
}

const confirmDelete = async () => {
  try {
    await deleteAnnouncement(deleteData.value.pk)
    ElMessage.success('删除成功')
    deleteVisible.value = false
    fetchData()
  } catch (error) {
    ElMessage.error('删除失败')
  }
}

onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.announcement-container {
  padding: 20px;
}

.search-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pagination-container {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.detail-content {
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.8;
}
</style>
