<script setup lang="ts">
import Button from 'primevue/button'

const emit = defineEmits<{ uploaded: [] }>()

const { t } = useI18n()
const { upload } = useApi()
const apiError = useApiError()
const notice = useNotice()
const config = useRuntimeConfig()
const current = useCurrentUser()

// O servidor informa o limite dele em `/auth/me/`; `NUXT_PUBLIC_MAX_UPLOAD_MB` é o plano B.
const maxMb = computed<number | null>(
  () => current.user?.limits?.max_upload_mb ?? (Number(config.public.maxUploadMb) || null),
)
onMounted(() => current.fetchUser())

const DONE = new Set(['completed', 'needs_review'])

const dragging = ref(false)
const busy = ref(false)
const input = ref<HTMLInputElement | null>(null)

async function send(files: File[]) {
  if (!files.length) return
  busy.value = true
  try {
    for (const file of files) {
      // A plataforma corta o corpo grande antes do backend: avisa já, com a mensagem traduzida.
      if (exceedsUploadLimit(file.size, maxMb.value)) {
        notice.error(t('errors.file_too_large', { max_mb: maxMb.value }))
        continue
      }
      const form = new FormData()
      form.append('file', file)
      try {
        const created = await upload<{ status?: string }>('/exam-documents/', form)
        // Na nuvem a leitura roda dentro da requisição: o laudo pode voltar já lido.
        if (DONE.has(created?.status ?? '')) notice.success(t('documents.uploadDone'))
        else notice.success(t('documents.uploadSuccess'))
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
    <p class="text-xs text-sepia-500">
      {{ maxMb ? $t('documents.uploadLimitWithSize', { max: maxMb }) : $t('documents.uploadLimit') }}
    </p>
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
