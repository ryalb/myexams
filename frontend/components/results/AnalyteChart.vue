<script setup lang="ts">
import type { HistoryPoint } from '@/types/exams'

const props = defineProps<{ points: HistoryPoint[]; unit: string }>()

const { t, n, d } = useI18n()
const canvas = ref<HTMLCanvasElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let chart: any = null

const COLORS: Record<string, string> = {
  high: '#a5423a',
  abnormal: '#a5423a',
  low: '#2e6c99',
  normal: '#4e3a1d',
  unknown: '#ad8a54',
}
const BAND_FILL = 'rgba(46, 108, 153, 0.16)'
const BAND_HALF_WIDTH = 16

const timestamp = (point: HistoryPoint) => (point.collectedAt ? new Date(point.collectedAt).getTime() : NaN)

const series = computed(() => props.points.filter((p) => p.value !== null && !Number.isNaN(timestamp(p))))

// Faixa de referência de cada ponto (cada laudo traz a sua). Faixas só com limite
// superior (< 190) ou só inferior (> 40) são fechadas no zero / no topo do gráfico.
function bandFor(point: HistoryPoint, ceiling: number): { low: number; high: number } | null {
  if (point.refLow === null && point.refHigh === null) return null
  return { low: point.refLow ?? 0, high: point.refHigh ?? ceiling }
}

// Uma faixa por ponto, desenhada como retângulo: funciona mesmo com um único
// laudo (uma área preenchida entre dois datasets exigiria pelo menos dois pontos).
const referenceBands = {
  id: 'referenceBands',
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  beforeDatasetsDraw(instance: any) {
    const { ctx, scales, chartArea } = instance
    if (!chartArea) return
    const rows = series.value
    const ceiling = Math.max(0, ...rows.map((p) => p.value as number), ...rows.map((p) => p.refHigh ?? 0)) * 1.15
    ctx.save()
    ctx.fillStyle = BAND_FILL
    for (const point of rows) {
      const band = bandFor(point, ceiling)
      if (!band) continue
      const x = scales.x.getPixelForValue(timestamp(point))
      const top = Math.max(chartArea.top, scales.y.getPixelForValue(band.high))
      const bottom = Math.min(chartArea.bottom, scales.y.getPixelForValue(band.low))
      if (bottom > top) ctx.fillRect(x - BAND_HALF_WIDTH, top, BAND_HALF_WIDTH * 2, bottom - top)
    }
    ctx.restore()
  },
}

function buildData() {
  const rows = series.value
  return {
    datasets: [
      {
        label: t('analyte.datasetValue'),
        data: rows.map((p) => ({ x: timestamp(p), y: p.value as number })),
        borderColor: '#6b4f26',
        borderWidth: 2,
        tension: 0,
        // Resultado "anterior impresso" aparece vazado: veio do histórico do laudo.
        pointBackgroundColor: rows.map((p) =>
          p.origin === 'lab_previous' ? '#ffffff' : (COLORS[p.flag] ?? COLORS.unknown),
        ),
        pointBorderColor: rows.map((p) => COLORS[p.flag] ?? COLORS.unknown),
        pointBorderWidth: 2,
        pointRadius: 5,
        pointHoverRadius: 7,
      },
    ],
  }
}

const DAY = 24 * 60 * 60 * 1000

// Marcas no eixo X em limites de ano (ou de mês, em séries curtas): datas
// "redondas" legíveis em vez de dias arbitrários que se sobrepõem.
function buildTicks(min: number, max: number) {
  const spanYears = (max - min) / (365 * DAY)
  const ticks: { value: number }[] = []
  if (spanYears < 2) {
    const cursor = new Date(min)
    cursor.setDate(1)
    cursor.setHours(0, 0, 0, 0)
    const step = spanYears < 0.6 ? 1 : 3
    while (cursor.getTime() <= max) {
      if (cursor.getTime() >= min) ticks.push({ value: cursor.getTime() })
      cursor.setMonth(cursor.getMonth() + step)
    }
    return { ticks, monthly: true }
  }
  const first = new Date(min).getFullYear()
  const last = new Date(max).getFullYear()
  const step = Math.max(1, Math.ceil((last - first + 1) / 7))
  for (let year = first; year <= last + 1; year += step) {
    const value = new Date(year, 0, 1).getTime()
    if (value >= min - 365 * DAY && value <= max + 365 * DAY) ticks.push({ value })
  }
  return { ticks, monthly: false }
}

function buildOptions() {
  const rows = series.value
  const times = rows.map(timestamp)
  const pad = Math.max((Math.max(...times) - Math.min(...times)) * 0.06, 30 * DAY)
  const min = Math.min(...times) - pad
  const max = Math.max(...times) + pad
  const { ticks, monthly } = buildTicks(min, max)
  const yBounds = [
    ...rows.map((p) => p.value as number),
    ...rows.flatMap((p) => [p.refLow, p.refHigh]).filter((v): v is number => v !== null),
  ]

  return {
    responsive: true,
    maintainAspectRatio: false,
    parsing: false,
    interaction: { mode: 'nearest', intersect: true },
    scales: {
      x: {
        type: 'linear',
        min,
        max,
        afterBuildTicks: (scale: { ticks: { value: number }[] }) => {
          scale.ticks = ticks
        },
        ticks: {
          callback: (value: number) => d(new Date(value), monthly ? 'monthYear' : 'year'),
        },
        grid: { color: '#f5eedd' },
      },
      y: {
        // Inclui os limites da faixa de referência: sem isso o eixo ajusta só aos
        // valores e a banda aparece cortada.
        suggestedMin: Math.min(...yBounds),
        suggestedMax: Math.max(...yBounds),
        title: { display: Boolean(props.unit), text: props.unit },
        ticks: { callback: (value: number) => n(value, 'decimal') },
        grid: { color: '#f5eedd' },
      },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          title: (items: { dataIndex: number }[]) => d(new Date(timestamp(rows[items[0].dataIndex])), 'short'),
          label: (item: { dataIndex: number }) => {
            const p = rows[item.dataIndex]
            const lines = [`${n(p.value as number, 'decimal')} ${props.unit}`.trim()]
            lines.push(t('analyte.tooltipPrinted', { value: p.valueRaw }))
            if (p.refLow !== null || p.refHigh !== null) {
              lines.push(
                t('analyte.tooltipReference', {
                  low: p.refLow === null ? t('common.empty') : n(p.refLow, 'decimal'),
                  high: p.refHigh === null ? t('common.empty') : n(p.refHigh, 'decimal'),
                }),
              )
            }
            if (p.laboratory) lines.push(t('analyte.tooltipLaboratory', { name: p.laboratory }))
            return lines
          },
        },
      },
    },
  }
}

function destroyChart() {
  chart?.destroy()
  chart = null
}

// Cada chamada recebe um número: se outra começar enquanto o `import()` do Chart.js
// ainda carrega, a mais antiga desiste em vez de criar um segundo gráfico.
let renderToken = 0
let mounted = false

async function render() {
  const token = ++renderToken
  // Faixa de datas sem pontos: o gráfico antigo não pode ficar na tela.
  if (!series.value.length) {
    destroyChart()
    return
  }
  const { Chart } = await import('chart.js/auto')
  // Durante o `await` o componente pode ter sido desmontado, o canvas removido ou
  // a série ter esvaziado.
  if (token !== renderToken || !mounted || !canvas.value || !series.value.length) return
  destroyChart()
  chart = new Chart(canvas.value, {
    type: 'line',
    data: buildData(),
    options: buildOptions(),
    plugins: [referenceBands],
  })
}

onMounted(() => {
  mounted = true
  render()
})
// `flush: 'post'`: o canvas já está no DOM quando a série volta a ter pontos.
watch(() => [props.points, props.unit], render, { deep: true, flush: 'post' })
onBeforeUnmount(() => {
  mounted = false
  renderToken += 1
  destroyChart()
})
</script>

<template>
  <div class="h-80 w-full">
    <p v-if="!series.length" class="flex h-full items-center justify-center text-sm text-sepia-500">
      {{ $t('analyte.noPointsInRange') }}
    </p>
    <!-- `v-show`: o canvas precisa continuar montado para o gráfico voltar quando a série ganhar pontos. -->
    <canvas v-show="series.length" ref="canvas" role="img" :aria-label="$t('analyte.chartTitle')" />
  </div>
</template>
