<script setup lang="ts">
// Mini-tendência em SVG: um gráfico Chart.js por linha de tabela seria pesado.
const props = withDefaults(defineProps<{ values: number[]; width?: number; height?: number }>(), {
  width: 96,
  height: 28,
})

const points = computed(() => {
  const values = props.values
  if (values.length < 2) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 3
  return values
    .map((value, i) => {
      const x = pad + (i / (values.length - 1)) * (props.width - pad * 2)
      const y = props.height - pad - ((value - min) / span) * (props.height - pad * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})
const last = computed(() => {
  const coords = points.value.split(' ')
  const [x, y] = (coords[coords.length - 1] ?? '').split(',')
  return { x: Number(x), y: Number(y) }
})
</script>

<template>
  <svg v-if="points" :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`" aria-hidden="true">
    <polyline :points="points" fill="none" stroke="#85652f" stroke-width="1.5" stroke-linejoin="round" />
    <circle :cx="last.x" :cy="last.y" r="2.5" fill="#33261a" />
  </svg>
  <span v-else class="text-sepia-500">{{ $t('common.empty') }}</span>
</template>
