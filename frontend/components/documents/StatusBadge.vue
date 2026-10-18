<script setup lang="ts">
const props = defineProps<{ status: string, done?: number, total?: number }>()

const STYLES: Record<string, string> = {
  uploaded: 'bg-sepia-100 text-sepia-700',
  extracting: 'bg-azul-100 text-azul-800',
  needs_review: 'bg-amber-100 text-amber-800',
  completed: 'bg-azul-50 text-azul-700',
  failed: 'bg-vermelho-100 text-vermelho-700',
}
const ICONS: Record<string, string> = {
  uploaded: 'mdi:tray-arrow-up',
  extracting: 'mdi:loading',
  needs_review: 'mdi:alert-circle-outline',
  completed: 'mdi:check-circle-outline',
  failed: 'mdi:close-circle-outline',
}
const style = computed(() => STYLES[props.status] ?? STYLES.uploaded)
const icon = computed(() => ICONS[props.status] ?? ICONS.uploaded)
// Só mostra a fração depois que o laudo foi dividido em blocos (total > 0).
const fraction = computed(() =>
  props.status === 'extracting' && (props.total ?? 0) > 0 ? `${props.done ?? 0}/${props.total}` : '',
)
</script>

<template>
  <span :class="['inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap', style]">
    <Icon :icon="icon" :class="['h-3.5 w-3.5', status === 'extracting' ? 'animate-spin' : '']" />
    {{ $t(`status.${status}`) }}
    <span v-if="fraction" class="tabular-nums">{{ fraction }}</span>
  </span>
</template>
