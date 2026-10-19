<script setup lang="ts">
import Popover from 'primevue/popover'
import type { ColumnVisibility } from '@/composables/useColumnVisibility'

const props = defineProps<{ cols: ColumnVisibility; id: string }>()

const popover = ref<InstanceType<typeof Popover> | null>(null)
const open = ref(false)
const toggleList = computed(() => props.cols.columns.value.filter((column) => !column.required))
</script>

<template>
  <div class="inline-block">
    <button
      type="button"
      class="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-sepia-200 bg-white px-3 text-sm text-sepia-700 hover:bg-sepia-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sepia-500 sm:min-h-8 sm:text-xs"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-controls="`${id}-columns`"
      @click="popover?.toggle($event)"
    >
      <Icon icon="mdi:view-column-outline" class="h-4 w-4" aria-hidden="true" />
      {{ $t('columns.button') }}
      <span
        v-if="cols.customized.value"
        class="h-1.5 w-1.5 rounded-full bg-azul-600"
        role="img"
        :aria-label="$t('columns.customized')"
      />
    </button>

    <Popover :id="`${id}-columns`" ref="popover" @show="open = true" @hide="open = false">
      <fieldset class="m-0 min-w-56 max-w-[80vw] border-0 p-0">
        <legend class="mb-1 px-1 text-xs font-semibold uppercase tracking-wide text-sepia-500">{{ $t('columns.title') }}</legend>
        <label
          v-for="column in toggleList"
          :key="column.key"
          class="flex min-h-11 cursor-pointer items-center gap-3 rounded px-1 text-sm text-sepia-800 hover:bg-sepia-50 sm:min-h-9"
        >
          <input
            type="checkbox"
            class="h-5 w-5 rounded border-sepia-300 sm:h-4 sm:w-4"
            :checked="cols.show(column.key)"
            @change="cols.toggle(column.key)"
          >
          {{ column.label }}
        </label>
        <button
          v-if="cols.customized.value"
          type="button"
          class="mt-1 min-h-11 w-full rounded px-1 text-left text-sm text-sepia-600 underline hover:text-sepia-900 sm:min-h-9"
          @click="cols.reset()"
        >
          {{ $t('columns.reset') }}
        </button>
      </fieldset>
    </Popover>
  </div>
</template>
