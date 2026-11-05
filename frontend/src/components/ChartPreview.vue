<template>
  <figure class="chart-preview" :aria-busy="state === 'loading'">
    <div class="chart-stage">
      <div v-if="state === 'loading'" class="image-notice" role="status">正在加载图表…</div>
      <div v-if="state === 'error'" class="image-notice image-error" role="status">
        <strong>暂时无法加载图表</strong>
        <span>可以重试；若历史图表已失效，请重新生成。</span>
        <button type="button" @click="retry">重新加载</button>
      </div>
      <a v-show="state !== 'error'" :href="source" target="_blank" rel="noopener noreferrer" :aria-label="`查看大图：${title}`">
        <img ref="imageRef" :key="attempt" :src="source" :alt="title" :class="{ loading: state === 'loading' }"
          referrerpolicy="no-referrer" @load="state = 'loaded'" @error="state = 'error'" />
      </a>
    </div>
    <figcaption><span>{{ title }}</span><a :href="source" target="_blank" rel="noopener noreferrer">查看原图 ↗</a></figcaption>
  </figure>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
const props = defineProps({ source: { type: String, required: true }, title: { type: String, default: '分析图表' } })
const state = ref('loading')
const attempt = ref(0)
const imageRef = ref(null)
function syncCachedImage() {
  if (imageRef.value?.complete && imageRef.value.naturalWidth > 0) state.value = 'loaded'
}
async function retry() {
  state.value = 'loading'
  attempt.value++
  await nextTick()
  syncCachedImage()
}
onMounted(syncCachedImage)
watch(() => props.source, retry)
</script>

<style scoped>
.chart-preview { margin: 12px 0 0; border: 1px solid #dce5e7; border-radius: 12px; overflow: hidden; background: #fff; }
.chart-stage { position: relative; min-height: 180px; display: grid; place-items: center; padding: 12px; }
.chart-stage a { width: 100%; text-align: center; }
img { max-width: 100%; max-height: 520px; width: auto; height: auto; vertical-align: middle; }
img.loading { opacity: 0; }
.image-notice { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 16px; color: #657b87; font-size: 13px; background: #f5f8f8; text-align: center; }
.image-error strong { color: #7d4f3d; }
button { border: 1px solid #b9cdd3; border-radius: 7px; padding: 6px 14px; background: #fff; color: #315366; cursor: pointer; }
figcaption { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 11px 14px; border-top: 1px solid #edf1f2; font-size: 12px; }
figcaption span { overflow-wrap: anywhere; }
a { color: #315f72; text-decoration: none; }
figcaption a { flex-shrink: 0; }
</style>
