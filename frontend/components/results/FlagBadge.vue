<script setup lang="ts">
// Situação do resultado em relação à faixa de referência do próprio laudo.
// Descreve onde o valor cai na faixa; não é diagnóstico.
const props = defineProps<{ flag: string }>()

const STYLES: Record<string, string> = {
  low: 'bg-azul-100 text-azul-800',
  normal: 'bg-sepia-100 text-sepia-700',
  high: 'bg-vermelho-100 text-vermelho-700',
  abnormal: 'bg-vermelho-100 text-vermelho-700',
  unknown: 'bg-sepia-50 text-sepia-500',
}
const ICONS: Record<string, string> = {
  low: 'mdi:arrow-down',
  normal: 'mdi:check',
  high: 'mdi:arrow-up',
  abnormal: 'mdi:alert-outline',
  unknown: 'mdi:minus',
}
const style = computed(() => STYLES[props.flag] ?? STYLES.unknown)
const icon = computed(() => ICONS[props.flag] ?? ICONS.unknown)
</script>

<template>
  <span :class="['inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap', style]">
    <Icon :icon="icon" class="h-3.5 w-3.5" />
    {{ $t(`flag.${flag}`) }}
  </span>
</template>
