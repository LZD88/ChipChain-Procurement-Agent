<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="brand-row">
        <div class="logo-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
        <div class="brand-copy"><strong>棱镜工作台</strong><span>智能供应链决策中心</span></div>
        <button class="sidebar-toggle" type="button" :aria-label="mobile ? '关闭会话侧栏' : '收起会话侧栏'" :title="mobile ? '关闭会话侧栏' : '收起会话侧栏'" aria-controls="session-sidebar" aria-expanded="true" @click="$emit('toggle-sidebar')"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16M6 8h.01M6 11h.01"/></svg></button>
      </div>

      <button class="new-chat-btn" type="button" @click="$emit('new-chat', null)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        新建会话
        <span class="shortcut">Ctrl N</span>
      </button>

      <label class="search-box">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/></svg>
        <input v-model.trim="searchKeyword" type="search" placeholder="搜索项目或会话" aria-label="搜索项目或会话" />
      </label>
    </div>

    <div class="navigation-scroll">
      <section class="nav-section">
        <div class="section-heading">
          <span>项目</span>
          <button type="button" title="创建项目" aria-label="创建项目" @click="openProjectDialog">＋</button>
        </div>

        <div v-if="visibleProjects.length" class="project-list">
          <div v-for="project in visibleProjects" :key="project.project_id" class="project-block">
            <div class="project-row" :class="{ active: currentProjectId === project.project_id }">
              <button
                class="project-expand"
                type="button"
                :aria-expanded="isExpanded(project.project_id)"
                :aria-label="isExpanded(project.project_id) ? '收起项目会话' : '展开项目会话'"
                @click="toggleProject(project.project_id)"
              >
                <svg class="chevron" :class="{ open: isExpanded(project.project_id) }" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>
              </button>
              <button class="project-select" type="button" @click="openProjectWorkspace(project)">
                <svg class="folder" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5h7l2-2h9v13H3z"/></svg>
                <span :title="project.name">{{ project.name }}</span>
                <em v-if="projectSessionCount(project.project_id)">{{ projectSessionCount(project.project_id) }}</em>
              </button>
              <button
                class="project-more project-menu-trigger"
                type="button"
                aria-label="打开项目菜单"
                title="项目详情"
                @click.stop="openProjectMenu(project, $event)"
              >•••</button>
              <button
                class="project-compose"
                type="button"
                :aria-label="`在“${project.name}”中新建会话`"
                :title="`在“${project.name}”中新建会话`"
                @click.stop="startProjectChat(project)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-7M16 3h5v5M11 13 21 3"/></svg>
              </button>
            </div>

            <div v-if="isExpanded(project.project_id)" class="project-sessions">
              <SessionRow
                v-for="session in sessionsForProject(project)"
                :key="session.thread_id"
                :session="session"
                :active="session.thread_id === currentThreadId"
                :menu-open="openSessionMenuId === session.thread_id"
                @toggle-menu="toggleSessionMenu(session.thread_id)"
                @close-menu="closeSessionMenu"
                @select="$emit('select-session', session.thread_id)"
                @pin="togglePin"
                @rename="openRenameDialog"
                @delete="openDeleteSessionDialog"
              />
              <p v-if="!sessionsForProject(project).length" class="project-empty">暂无会话，点击右侧图标开始</p>
            </div>
          </div>
        </div>
        <button v-else-if="!searchKeyword" class="create-first-project" type="button" @click="openProjectDialog">
          <span>＋</span><strong>创建第一个项目</strong><small>按业务主题归纳相关会话</small>
        </button>
        <p v-else class="no-result">没有匹配的项目</p>
      </section>

      <section class="nav-section recent-section">
        <div class="section-heading"><span>独立会话</span><em>{{ standaloneSessions.length }}</em></div>
        <SessionRow
          v-for="session in standaloneSessions"
          :key="session.thread_id"
          :session="session"
          :active="session.thread_id === currentThreadId"
          :menu-open="openSessionMenuId === session.thread_id"
          @toggle-menu="toggleSessionMenu(session.thread_id)"
          @close-menu="closeSessionMenu"
          @select="$emit('select-session', session.thread_id)"
          @pin="togglePin"
          @rename="openRenameDialog"
          @delete="openDeleteSessionDialog"
        />
        <p v-if="!standaloneSessions.length && currentThreadId !== null" class="no-result">暂无独立会话</p>
      </section>
    </div>

    <div class="sidebar-footer">
      <div class="account-avatar" aria-hidden="true">{{ username.slice(0, 1).toUpperCase() }}</div>
      <div class="account-copy"><strong>{{ username }}</strong><span>采购决策工作区</span></div>
      <button class="logout-btn" type="button" :disabled="loggingOut" @click="$emit('logout')" :title="loggingOut ? '正在退出' : '退出登录'">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H5v14h5M14 8l4 4-4 4m4-4H9"/></svg>
      </button>
    </div>

    <div v-if="dialog.type" class="dialog-backdrop" @click.self="closeDialog">
      <form class="dialog-card" @submit.prevent="submitDialog">
        <div class="dialog-icon" aria-hidden="true">{{ dialog.type === 'rename' ? '✎' : '⌂' }}</div>
        <div class="dialog-copy">
          <h3>{{ dialog.type === 'project' ? '创建项目' : dialog.type === 'edit-project' ? '编辑项目' : '修改会话名称' }}</h3>
          <p>{{ dialog.type === 'project' ? '项目可以集中管理同一采购主题下的多个会话。' : dialog.type === 'edit-project' ? '修改项目名称后，项目内已有会话不会受到影响。' : '使用简短、明确的名称，方便后续快速定位。' }}</p>
        </div>
        <label>
          <span>{{ dialog.type === 'rename' ? '会话名称' : '项目名称' }}</span>
          <input ref="dialogInput" v-model="dialog.value" :maxlength="dialog.type === 'rename' ? 80 : 40" required />
        </label>
        <div class="dialog-actions">
          <button type="button" @click="closeDialog">取消</button>
          <button class="primary" type="submit" :disabled="!dialog.value.trim()">{{ dialog.type === 'project' ? '创建并开始会话' : '保存名称' }}</button>
        </div>
      </form>
    </div>

  </aside>

  <ConfirmDialog
    v-if="sessionDeleteTarget"
    title="删除会话？"
    :description="`将永久删除会话“${sessionDeleteTarget.title}”及其历史消息，此操作无法撤销。`"
    @cancel="sessionDeleteTarget = null"
    @confirm="confirmDeleteSession"
  />
  <ConfirmDialog
    v-if="deleteTarget"
    title="删除项目？"
    :description="`删除项目“${deleteTarget.name}”后，项目内的 ${projectSessionCount(deleteTarget.project_id)} 个会话会移至“独立会话”，历史消息和采购记录会保留。`"
    @cancel="deleteTarget = null"
    @confirm="confirmDeleteProject"
  />

  <Teleport to="body">
    <div
      v-if="projectMenu.project"
      class="project-popover"
      :style="{ top: `${projectMenu.top}px`, left: `${projectMenu.left}px` }"
    >
      <div class="popover-heading">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5h7l2-2h9v13H3z"/></svg>
        <strong>{{ projectMenu.project.name }}</strong>
        <span>项目</span>
      </div>
      <button type="button" @click="openProjectWorkspace(projectMenu.project)">
        <span class="popover-row-icon">◯</span>
        <span><strong>{{ projectSessionCount(projectMenu.project.project_id) }} 个会话</strong><small>查看项目内的全部采购对话</small></span>
      </button>
      <button type="button" @click="startProjectChat(projectMenu.project)">
        <span class="popover-row-icon">＋</span>
        <span><strong>新建项目会话</strong><small>在当前项目上下文中开始</small></span>
      </button>
      <div class="popover-divider"></div>
      <button type="button" @click="openEditProjectDialog(projectMenu.project)">
        <span class="popover-row-icon">⚙</span>
        <span><strong>编辑项目</strong><small>修改项目名称</small></span>
      </button>
      <button class="popover-danger" type="button" @click="openDeleteProjectDialog(projectMenu.project)">
        <span class="popover-row-icon">⌫</span>
        <span><strong>删除项目</strong><small>会话保留并移至独立会话</small></span>
      </button>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import SessionRow from './SessionRow.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const props = defineProps({
  username: { type: String, required: true },
  mobile: Boolean,
  loggingOut: Boolean,
  sessions: { type: Array, default: () => [] },
  projects: { type: Array, default: () => [] },
  currentThreadId: { type: String, default: null },
  pendingProjectId: { type: String, default: null },
  currentProjectId: { type: String, default: null }
})

const emit = defineEmits(['select-session', 'new-chat', 'open-project', 'delete-session', 'update-session', 'create-project', 'update-project', 'delete-project', 'logout', 'toggle-sidebar'])
const searchKeyword = ref('')
const expandedProjects = ref(new Set())
const dialogInput = ref(null)
const dialog = reactive({ type: null, value: '', session: null, project: null })
const projectMenu = reactive({ project: null, top: 0, left: 0 })
const openSessionMenuId = ref(null)
const deleteTarget = ref(null)
const sessionDeleteTarget = ref(null)

watch([() => props.currentThreadId, () => props.currentProjectId], () => {
  closeSessionMenu()
  closeProjectMenu()
})

watch(() => props.projects, projects => {
  const next = new Set(expandedProjects.value)
  projects.forEach(project => next.add(project.project_id))
  expandedProjects.value = next
}, { immediate: true })

watch(() => props.pendingProjectId, projectId => {
  if (!projectId) return
  const next = new Set(expandedProjects.value)
  next.add(projectId)
  expandedProjects.value = next
})

const normalizedSearch = computed(() => searchKeyword.value.toLocaleLowerCase())
const visibleProjects = computed(() => {
  if (!normalizedSearch.value) return props.projects
  return props.projects.filter(project =>
    project.name.toLocaleLowerCase().includes(normalizedSearch.value) ||
    props.sessions.some(session => session.project_id === project.project_id && matchesSearch(session))
  )
})
const standaloneSessions = computed(() => props.sessions.filter(session => !session.project_id && matchesSearch(session)))

function matchesSearch(session) {
  return !normalizedSearch.value || session.title.toLocaleLowerCase().includes(normalizedSearch.value)
}

function sessionsForProject(project) {
  const projectNameMatches = normalizedSearch.value && project.name.toLocaleLowerCase().includes(normalizedSearch.value)
  return props.sessions.filter(session =>
    session.project_id === project.project_id && (projectNameMatches || matchesSearch(session))
  )
}

function projectSessionCount(projectId) {
  return props.sessions.filter(session => session.project_id === projectId).length
}

function isExpanded(projectId) {
  return normalizedSearch.value || expandedProjects.value.has(projectId)
}

function toggleProject(projectId) {
  const next = new Set(expandedProjects.value)
  next.has(projectId) ? next.delete(projectId) : next.add(projectId)
  expandedProjects.value = next
}

function startProjectChat(project) {
  const next = new Set(expandedProjects.value)
  next.add(project.project_id)
  expandedProjects.value = next
  closeProjectMenu()
  emit('new-chat', project.project_id)
}

function openProjectWorkspace(project) {
  const next = new Set(expandedProjects.value)
  next.add(project.project_id)
  expandedProjects.value = next
  closeProjectMenu()
  emit('open-project', project.project_id)
}

function openProjectMenu(project, event) {
  closeSessionMenu()
  if (projectMenu.project?.project_id === project.project_id) {
    closeProjectMenu()
    return
  }
  const row = event.currentTarget.closest('.project-row')
  const rect = row.getBoundingClientRect()
  projectMenu.project = project
  projectMenu.top = Math.max(12, Math.min(rect.top, window.innerHeight - 254))
  projectMenu.left = Math.max(12, Math.min(rect.right + 8, window.innerWidth - 310))
}

function closeProjectMenu() {
  projectMenu.project = null
}

function toggleSessionMenu(threadId) {
  closeProjectMenu()
  openSessionMenuId.value = openSessionMenuId.value === threadId ? null : threadId
}

function closeSessionMenu() {
  openSessionMenuId.value = null
}

function togglePin(session) {
  emit('update-session', { threadId: session.thread_id, changes: { pinned: !session.pinned } })
}

function openDeleteSessionDialog(session) {
  closeSessionMenu()
  closeProjectMenu()
  sessionDeleteTarget.value = session
}

function confirmDeleteSession() {
  if (!sessionDeleteTarget.value) return
  const threadId = sessionDeleteTarget.value.thread_id
  sessionDeleteTarget.value = null
  emit('delete-session', threadId)
}

function openProjectDialog() {
  dialog.type = 'project'
  dialog.value = ''
  dialog.session = null
  dialog.project = null
  focusDialog()
}

function openRenameDialog(session) {
  dialog.type = 'rename'
  dialog.value = session.title
  dialog.session = session
  dialog.project = null
  focusDialog()
}

function openEditProjectDialog(project) {
  closeProjectMenu()
  dialog.type = 'edit-project'
  dialog.value = project.name
  dialog.session = null
  dialog.project = project
  focusDialog()
}

function openDeleteProjectDialog(project) {
  closeProjectMenu()
  deleteTarget.value = project
}

function confirmDeleteProject() {
  if (!deleteTarget.value) return
  emit('delete-project', deleteTarget.value.project_id)
  deleteTarget.value = null
}

function focusDialog() {
  nextTick(() => {
    dialogInput.value?.focus()
    dialogInput.value?.select()
  })
}

function closeDialog() {
  dialog.type = null
  dialog.value = ''
  dialog.session = null
  dialog.project = null
}

function submitDialog() {
  const value = dialog.value.trim()
  if (!value) return
  if (dialog.type === 'project') emit('create-project', value)
  else if (dialog.type === 'edit-project' && dialog.project) emit('update-project', { projectId: dialog.project.project_id, changes: { name: value } })
  else if (dialog.session) emit('update-session', { threadId: dialog.session.thread_id, changes: { title: value } })
  closeDialog()
}

function handleKeyboard(event) {
  if (event.key === 'Escape' && openSessionMenuId.value) {
    closeSessionMenu()
    return
  }
  if (event.key === 'Escape' && deleteTarget.value) {
    deleteTarget.value = null
    return
  }
  if (event.key === 'Escape' && projectMenu.project) {
    closeProjectMenu()
    return
  }
  if (event.key === 'Escape' && dialog.type) {
    closeDialog()
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'n') {
    event.preventDefault()
    emit('new-chat', null)
  }
}

function handleDocumentPointer(event) {
  if (!event.target.closest?.('.session-menu')) closeSessionMenu()
  if (!projectMenu.project) return
  if (event.target.closest?.('.project-popover, .project-menu-trigger')) return
  closeProjectMenu()
}

onMounted(() => {
  window.addEventListener('keydown', handleKeyboard)
  document.addEventListener('pointerdown', handleDocumentPointer, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeyboard)
  document.removeEventListener('pointerdown', handleDocumentPointer, true)
})
</script>

<style scoped>
.sidebar { width: 100%; height: 100%; display: flex; flex-direction: column; overflow: hidden; position: relative; background: var(--nav-bg); color: var(--nav-text); }
.sidebar-header { padding: 18px 14px 14px; }
.brand-row { display: flex; align-items: center; gap: 9px; margin: 0 0 22px; }
.sidebar-toggle { display: grid; place-items: center; flex: 0 0 30px; width: 30px; height: 32px; margin-left: auto; padding: 5px; border: 0; border-radius: 7px; background: transparent; color: var(--nav-muted); cursor: pointer; }
.sidebar-toggle:hover { background: var(--nav-hover); color: var(--nav-text); }
.sidebar-toggle:focus-visible { outline: 2px solid var(--nav-accent); outline-offset: 2px; }
.sidebar-toggle svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
.logo-mark { width: 32px; height: 32px; display: grid; flex: 0 0 32px; grid-template-columns: repeat(2, 1fr); gap: 3px; padding: 7px; border-radius: 10px; background: var(--color-primary); }
.logo-mark i { border: 1.5px solid var(--color-white); border-radius: 50%; }
.brand-copy { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.brand-copy strong { color: var(--nav-text); font-size: 14px; letter-spacing: .02em; white-space: nowrap; }
.brand-copy span { color: var(--nav-muted); font-size: 11px; white-space: nowrap; }
.new-chat-btn { display: flex; width: 100%; align-items: center; gap: 9px; padding: 11px 12px; border: 1px solid var(--color-primary); border-radius: 11px; background: var(--color-primary); color: var(--color-white); cursor: pointer; font-size: 13px; font-weight: 650; box-shadow: 0 5px 12px rgba(0, 0, 0, .10); transition: .16s ease; }
.new-chat-btn:hover { transform: translateY(-1px); background: var(--color-primary-hover); box-shadow: 0 8px 16px rgba(0, 0, 0, .14); }
.new-chat-btn svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }
.shortcut { margin-left: auto; color: var(--nav-accent); font-size: 9px; font-weight: 500; letter-spacing: .04em; }
.search-box { display: flex; align-items: center; gap: 8px; margin-top: 11px; padding: 9px 11px; border: 1px solid var(--nav-border); border-radius: 10px; background: var(--nav-surface); transition: .16s ease; }
.search-box:focus-within { border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(22, 134, 109, .16); }
.search-box svg { width: 16px; height: 16px; flex: 0 0 16px; fill: none; stroke: var(--nav-muted); stroke-width: 1.8; stroke-linecap: round; }
.search-box input { width: 100%; border: 0; outline: 0; background: transparent; color: var(--nav-text); font-size: 12px; }
.search-box input::placeholder { color: var(--nav-muted); }
.navigation-scroll { flex: 1; min-height: 0; overflow-x: hidden; overflow-y: auto; padding: 13px 11px 18px; }
.navigation-scroll::-webkit-scrollbar { width: 4px; }
.navigation-scroll::-webkit-scrollbar-thumb { border-radius: 3px; background: var(--nav-border); }
.nav-section + .nav-section { margin-top: 18px; }
.section-heading { display: flex; align-items: center; gap: 7px; min-height: 28px; padding: 0 8px; color: var(--nav-muted); font-size: 10px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
.section-heading button { display: grid; width: 25px; height: 25px; margin-left: auto; place-items: center; border: 0; border-radius: 7px; background: transparent; color: var(--nav-muted); cursor: pointer; font-size: 18px; line-height: 1; }
.section-heading button:hover { background: var(--nav-hover); color: var(--nav-accent); }
.section-heading em { margin-left: auto; font-style: normal; letter-spacing: 0; }
.project-block { margin: 2px 0 7px; }
.project-row { display: flex; align-items: center; gap: 3px; border-radius: 10px; }
.project-row:hover, .project-row.active { background: var(--nav-active); }
.project-row.active { box-shadow: inset 3px 0 var(--nav-accent); }
.project-expand { display: grid; width: 22px; height: 31px; flex: 0 0 22px; place-items: center; padding: 0; border: 0; background: transparent; color: var(--nav-muted); cursor: pointer; }
.project-expand .chevron { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; transition: transform .16s ease; }
.project-expand .chevron.open { transform: rotate(90deg); }
.project-select { display: flex; flex: 1; min-width: 0; align-items: center; gap: 7px; padding: 8px 2px; border: 0; background: transparent; color: var(--nav-text); cursor: pointer; text-align: left; }
.project-select .folder { width: 17px; height: 17px; flex: 0 0 17px; fill: none; stroke: var(--nav-accent); stroke-width: 1.7; stroke-linejoin: round; }
.project-select span { overflow: hidden; flex: 1; font-size: 12px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.project-select em { min-width: 14px; color: var(--nav-muted); font-size: 9px; font-style: normal; text-align: center; }
.project-more, .project-compose { display: grid; width: 26px; height: 26px; flex: 0 0 26px; place-items: center; padding: 0; border: 0; border-radius: 7px; background: transparent; color: var(--nav-muted); cursor: pointer; opacity: 0; }
.project-more { font-size: 12px; letter-spacing: 1px; }
.project-compose { margin-right: 3px; }
.project-compose svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.project-row:hover .project-more, .project-row:hover .project-compose, .project-row:focus-within .project-more, .project-row:focus-within .project-compose, .project-row.active .project-compose { opacity: 1; }
.project-more:hover, .project-compose:hover { background: var(--nav-hover); color: var(--nav-accent); }
.project-sessions { margin-left: 18px; padding-left: 8px; border-left: 1px solid var(--nav-border); }
.project-empty, .no-result { margin: 6px 8px 11px; color: var(--nav-muted); font-size: 10px; line-height: 1.5; }
.pending-session { display: flex; align-items: center; gap: 8px; margin: 4px 0; padding: 8px 9px; border: 1px dashed var(--nav-border); border-radius: 9px; background: var(--nav-surface); color: var(--nav-muted); font-size: 10px; }
.pending-session span { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: var(--nav-accent); box-shadow: 0 0 0 3px var(--nav-active); }
.pending-session.standalone { margin: 2px 0 5px; }
.create-first-project { display: grid; width: calc(100% - 8px); grid-template-columns: 31px 1fr; margin: 4px; padding: 11px; border: 1px dashed var(--nav-border); border-radius: 11px; background: var(--nav-surface); color: var(--nav-text); cursor: pointer; text-align: left; }
.create-first-project > span { grid-row: 1 / 3; align-self: center; color: var(--nav-accent); font-size: 20px; }
.create-first-project strong { font-size: 11px; }
.create-first-project small { margin-top: 2px; color: var(--nav-muted); font-size: 9px; }
.sidebar-footer { display: flex; align-items: center; gap: 9px; padding: 14px 17px; border-top: 1px solid var(--nav-border); background: var(--nav-bg); }
.account-avatar { display: grid; width: 34px; height: 34px; flex: 0 0 34px; place-items: center; border-radius: 10px; background: var(--nav-hover); color: var(--nav-accent); font-size: 13px; font-weight: 700; }
.account-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }
.account-copy strong { overflow: hidden; color: var(--nav-text); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.account-copy span { color: var(--nav-muted); font-size: 9px; }
.logout-btn { display: grid; width: 30px; height: 30px; place-items: center; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--nav-muted); cursor: pointer; }
.logout-btn:hover { border-color: var(--nav-border); background: var(--nav-hover); color: var(--nav-accent); }
.logout-btn:disabled { cursor: wait; opacity: .5; }
.logout-btn svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.dialog-backdrop { position: fixed; z-index: 100; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(15, 23, 42, .40); backdrop-filter: blur(3px); }
.dialog-card { width: min(420px, 100%); display: grid; grid-template-columns: 46px 1fr; gap: 0 13px; padding: 24px; border: 1px solid var(--color-border); border-radius: 18px; background: var(--color-bg-secondary); box-shadow: 0 24px 70px rgba(15, 23, 42, .18); }
.dialog-icon { display: grid; width: 46px; height: 46px; grid-row: 1 / 3; place-items: center; border-radius: 13px; background: var(--color-bg-tertiary); color: var(--color-primary); font-size: 21px; }
.dialog-copy h3 { margin: 1px 0 5px; color: var(--color-text); font-size: 17px; }
.dialog-copy p { margin: 0; color: var(--color-text-secondary); font-size: 11px; line-height: 1.5; }
.dialog-card label { display: flex; grid-column: 1 / 3; flex-direction: column; gap: 7px; margin-top: 20px; }
.dialog-card label span { color: var(--color-text-secondary); font-size: 11px; font-weight: 650; }
.dialog-card input { padding: 11px 12px; border: 1px solid var(--color-border); border-radius: 10px; outline: none; background: #fff; color: var(--color-text); font-size: 13px; }
.dialog-card input:focus { border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-focus); }
.dialog-actions { display: flex; grid-column: 1 / 3; justify-content: flex-end; gap: 8px; margin-top: 20px; }
.dialog-actions button { padding: 9px 14px; border: 1px solid var(--color-border); border-radius: 9px; background: var(--color-bg-secondary); color: var(--color-text-secondary); cursor: pointer; font-size: 11px; }
.dialog-actions button.primary { border-color: var(--color-primary); background: var(--color-primary); color: #fff; }
.dialog-actions button:disabled { cursor: not-allowed; opacity: .5; }
.project-popover { position: fixed; z-index: 90; width: 292px; overflow: hidden; padding: 7px; border: 1px solid var(--color-border); border-radius: 14px; background: var(--color-bg-secondary); box-shadow: 0 18px 48px rgba(15, 23, 42, .14); color: var(--color-text); }
.popover-heading { display: flex; align-items: center; gap: 8px; padding: 8px 9px 10px; }
.popover-heading svg { width: 17px; height: 17px; fill: none; stroke: var(--color-primary); stroke-width: 1.7; stroke-linejoin: round; }
.popover-heading strong { overflow: hidden; flex: 1; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.popover-heading > span { padding: 2px 6px; border-radius: 5px; background: var(--color-bg-tertiary); color: var(--color-text-secondary); font-size: 8px; }
.project-popover > button { display: flex; width: 100%; align-items: center; gap: 9px; padding: 8px 9px; border: 0; border-radius: 8px; background: transparent; color: var(--color-text); cursor: pointer; text-align: left; }
.project-popover > button:hover { background: var(--color-bg-tertiary); }
.project-popover > button.popover-danger { color: #a34e3c; }
.project-popover > button.popover-danger:hover { background: #faeae5; }
.popover-row-icon { display: grid; width: 22px; height: 22px; flex: 0 0 22px; place-items: center; color: var(--color-text-secondary); font-size: 12px; }
.project-popover button > span:last-child { display: flex; min-width: 0; flex-direction: column; gap: 1px; }
.project-popover button strong { font-size: 11px; font-weight: 650; }
.project-popover button small { color: var(--color-text-secondary); font-size: 9px; }
.popover-divider { height: 1px; margin: 5px 1px; background: var(--color-border); }
@media (hover: none) { .project-more, .project-compose { opacity: 1; } }
@media (max-width: 760px) { .sidebar { height: 100%; } }
</style>
