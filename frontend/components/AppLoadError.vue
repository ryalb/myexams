<script setup lang="ts">
import Button from 'primevue/button'

// Falha de carregamento (rede, 500). Distinta do estado "vazio" ou "não
// encontrado": o usuário não deve concluir que não tem dados quando só não os
// conseguimos buscar.
defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div
    role="alert"
    class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-vermelho-200 bg-vermelho-50 px-4 py-3 text-sm text-vermelho-800"
  >
    <span class="flex items-center gap-2">
      <Icon icon="mdi:alert-circle-outline" class="h-5 w-5 shrink-0" aria-hidden="true" />
      {{ $t('messages.loadError') }}
    </span>
    <Button
      type="button"
      :disabled="loading"
      class="!rounded-md !border !border-vermelho-200 !bg-white !px-3 !py-1.5 !text-sm !text-vermelho-700 hover:!bg-vermelho-100 disabled:!opacity-50"
      @click="emit('retry')"
    >
      {{ $t('common.retry') }}
    </Button>
  </div>
</template>
