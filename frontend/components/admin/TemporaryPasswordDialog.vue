<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'

// Mostra a senha temporária UMA vez (criação de usuário ou redefinição). O pai zera `password`
// ao fechar: nada fica guardado depois daqui.
const props = defineProps<{
  email: string
  password: string
}>()
const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ closed: [] }>()

// O diálogo é modal: um aviso global ficaria atrás da máscara, então o resultado da cópia
// aparece aqui dentro (região viva, lida por leitores de tela).
const copyStatus = ref<'idle' | 'copied' | 'failed'>('idle')
const passwordBox = ref<HTMLElement | null>(null)
let opener: HTMLElement | null = null

watch(
  visible,
  (open) => {
    if (!open) return
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    copyStatus.value = 'idle'
  },
  { flush: 'sync' },
)

function selectPassword() {
  const element = passwordBox.value
  if (!element) return
  const range = document.createRange()
  range.selectNodeContents(element)
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
}

async function copy() {
  try {
    await navigator.clipboard.writeText(props.password)
    copyStatus.value = 'copied'
  } catch {
    // Sem permissão ou fora de contexto seguro: deixa a senha selecionada para Ctrl+C.
    copyStatus.value = 'failed'
    selectPassword()
  }
}

const onHide = () => {
  copyStatus.value = 'idle'
  if (opener?.isConnected) opener.focus()
  opener = null
  emit('closed')
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    :closable="false"
    :close-on-escape="false"
    :header="$t('admin.tempTitle')"
    class="w-[30rem] max-w-[95vw]"
    @after-hide="onHide"
  >
    <div class="space-y-4">
      <p class="text-sm text-sepia-700">{{ $t('admin.tempFor', { email }) }}</p>

      <div class="flex items-stretch gap-2">
        <code
          ref="passwordBox"
          class="flex-1 select-all break-all rounded-md border border-sepia-300 bg-sepia-50 px-3 py-2 font-mono text-base tracking-wide text-sepia-900"
          :aria-label="$t('admin.tempPasswordLabel')"
        >{{ password }}</code>
        <Button
          type="button"
          :aria-label="$t('admin.copyPassword')"
          class="!flex !items-center !gap-1.5 !rounded-md !border !border-sepia-300 !bg-white !px-3 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50"
          @click="copy"
        >
          <Icon icon="mdi:content-copy" class="h-4 w-4" aria-hidden="true" />
          {{ $t('admin.copy') }}
        </Button>
      </div>

      <div aria-live="polite" class="min-h-5 text-xs">
        <span v-if="copyStatus === 'copied'" class="text-azul-700">{{ $t('admin.copied') }}</span>
        <span v-else-if="copyStatus === 'failed'" class="text-vermelho-700">{{ $t('admin.copyFailed') }}</span>
      </div>

      <p class="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
        <Icon icon="mdi:alert-outline" class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        {{ $t('admin.tempWarning') }}
      </p>

      <div class="flex justify-end">
        <Button
          type="button"
          class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950"
          @click="visible = false"
        >
          {{ $t('admin.tempDone') }}
        </Button>
      </div>
    </div>
  </Dialog>
</template>
