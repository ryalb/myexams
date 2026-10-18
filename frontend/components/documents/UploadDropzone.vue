<script setup lang="ts">
import Button from 'primevue/button'

const emit = defineEmits<{ uploaded: [] }>()

const { t } = useI18n()
const { upload } = useApi()
const apiError = useApiError()
const notice = useNotice()

const dragging = ref(false)
const busy = ref(false)
const input = ref<HTMLInputElement | null>(null)

async function send(files: File[]) {
  if (!files.length) return
  busy.value = true
  try {
    for (const file of files) {
      const form = new FormData()
      form.append('file', file)
      try {
        await upload('/exam-documents/', form)
        notice.success(t('documents.uploadSuccess'))
        emit('uploaded')
      } catch (error) {
        notice.error(apiError.message(error))
      }
    }
  } finally {
    busy.value = false
    if (input.value) input.value.value = ''
  }
}

const onDrop = (event: DragEvent) => {
  dragging.value = false
  send(Array.from(event.dataTransfer?.files ?? []))
}
const onPick = (event: Event) => send(Array.from((event.target as HTMLInputElement).files ?? []))
const openPicker = () => input.value?.click()
</script>

<template>
  <section
    :aria-label="$t('documents.uploadTitle')"
    :class="[
      'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors',
      dragging ? 'border-sepia-500 bg-sepia-100' : 'border-sepia-300 bg-white',
    ]"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <Icon :icon="busy ? 'mdi:loading' : 'mdi:file-upload-outline'" :class="['h-10 w-10 text-sepia-500', busy ? 'animate-spin' : '']" />
    <p class="text-sm font-medium text-sepia-900">
      {{ busy ? $t('documents.uploading') : $t('documents.uploadHint') }}
    </p>
    <p class="text-xs text-sepia-500">{{ $t('documents.uploadLimit') }}</p>
    <Button
      type="button"
      :disabled="busy"
      class="!mt-2 !rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
      @click="openPicker"
    >
      {{ $t('documents.chooseFile') }}
    </Button>
    <!-- Visualmente oculto, mas com nome acessível: `display: none` esconderia o
         controle de leitores de tela. O foco por teclado fica no botão acima. -->
    <input
      ref="input"
      type="file"
      accept="application/pdf,.pdf"
      multiple
      tabindex="-1"
      :aria-label="$t('documents.fileInputLabel')"
      class="sr-only"
      @change="onPick"
    >
  </section>
</template>
