<script setup lang="ts">
import Button from 'primevue/button'
import type { UnreadBlock, WarningItem } from '@/types/exams'

// Blocos do laudo que a leitura automática encontrou mas não conseguiu ler (`block_unreadable`).
// Cada item pode virar um resultado digitado ou ser dispensado ("Marcar como resolvido").
const props = defineProps<{
  warnings: WarningItem[]
  // Há uma ação em andamento (ou o laudo ainda está sendo lido): os botões ficam desativados.
  busy: boolean
}>()
const emit = defineEmits<{
  add: [block: UnreadBlock]
  dismiss: [block: UnreadBlock]
}>()

const { t } = useI18n()

const blocks = computed(() =>
  props.warnings.filter((w): w is UnreadBlock => w.code === 'block_unreadable' && Boolean(w.params?.page)),
)

const label = (block: UnreadBlock) =>
  block.params.title
    ? t('document.unreadableItem', { page: block.params.page, title: block.params.title })
    : t('document.unreadableItemPageOnly', { page: block.params.page })

const heading = ref<HTMLElement | null>(null)
defineExpose({ focus: () => heading.value?.focus() })
</script>

<template>
  <section
    v-if="blocks.length"
    aria-labelledby="unread-blocks-title"
    class="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 shadow-sm"
  >
    <h2 id="unread-blocks-title" ref="heading" tabindex="-1" class="flex items-center gap-2 font-semibold outline-none">
      <Icon icon="mdi:alert-circle-outline" class="h-5 w-5 shrink-0" />
      {{ $t('document.unreadableTitle') }} ({{ blocks.length }})
    </h2>
    <p class="mt-1">{{ $t('document.unreadableIntro') }}</p>

    <ul class="mt-3 divide-y divide-amber-200 rounded-md border border-amber-200 bg-white">
      <li
        v-for="block in blocks"
        :key="`${block.params.page}-${block.params.title ?? ''}`"
        class="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
      >
        <span class="font-medium text-sepia-900">{{ label(block) }}</span>
        <span class="flex flex-wrap gap-2">
          <Button
            type="button"
            :disabled="busy"
            :aria-label="$t('document.unreadableAddFor', { item: label(block) })"
            class="!rounded-md !bg-sepia-900 !px-3 !py-1.5 !text-xs !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
            @click="emit('add', block)"
          >
            <Icon icon="mdi:plus" class="h-4 w-4" />
            {{ $t('document.unreadableAdd') }}
          </Button>
          <Button
            type="button"
            :disabled="busy"
            :aria-label="$t('document.unreadableDismissFor', { item: label(block) })"
            class="!rounded-md !border !border-sepia-300 !bg-white !px-3 !py-1.5 !text-xs !text-sepia-800 hover:!bg-sepia-50 disabled:!opacity-50"
            @click="emit('dismiss', block)"
          >
            <Icon icon="mdi:check" class="h-4 w-4" />
            {{ $t('document.unreadableDismiss') }}
          </Button>
        </span>
      </li>
    </ul>
  </section>
</template>
