<script setup lang="ts">
import { useSidebarProvider } from '@/composables/useSidebar'
import { usePageLoading } from '@/composables/usePageLoading'

const { isExpanded, isHovered } = useSidebarProvider()
const { pageLoading } = usePageLoading()
const { connect, disconnect } = useDocumentEvents()
const { notice, dismiss } = useNotice()

onMounted(connect)
onBeforeUnmount(disconnect)
</script>

<template>
  <div class="min-h-dvh bg-sepia-950 xl:flex">
    <LayoutAppSidebar />
    <LayoutBackdrop />
    <div
      class="min-h-dvh min-w-0 flex-1 bg-sepia-50 text-sepia-900 transition-all duration-300 ease-in-out"
      :class="[isExpanded || isHovered ? 'lg:ml-[290px]' : 'lg:ml-[90px]']"
    >
      <LayoutAppHeader />
      <main class="p-4 mx-auto max-w-screen-2xl md:p-6">
        <!-- A região viva fica sempre montada: leitores de tela só anunciam mudanças
             dentro de uma região que já existia. O papel (alert/status) vai no aviso. -->
        <div aria-live="polite" aria-atomic="true" class="sticky top-[4.75rem] z-[800]">
          <div
            v-if="notice"
            :role="notice.kind === 'error' ? 'alert' : 'status'"
            :class="[
              'mb-4 flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm',
              notice.kind === 'success'
                ? 'border-azul-200 bg-azul-50 text-azul-800'
                : 'border-vermelho-200 bg-vermelho-50 text-vermelho-800',
            ]"
          >
            <span>{{ notice.text }}</span>
            <button type="button" class="-m-2 shrink-0 p-2" :aria-label="$t('common.close')" @click="dismiss">
              <Icon icon="mdi:close" class="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div
          v-if="pageLoading"
          role="status"
          aria-live="polite"
          class="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-sepia-500"
        >
          <AppSpinner class="text-sepia-700" />
          <span class="text-sm">{{ $t('common.loading') }}</span>
        </div>
        <!-- `v-show`, não `v-if`: a página precisa estar montada por baixo do
             spinner para disparar as próprias requisições. -->
        <div v-show="!pageLoading">
          <slot />
        </div>
      </main>
    </div>
  </div>
</template>
