<script setup lang="ts">
import { useRoute } from 'vue-router'
import Avatar from 'primevue/avatar'
import { useSidebar } from '@/composables/useSidebar'

const route = useRoute()
const { isExpanded, isMobileOpen, isHovered } = useSidebar()
const currentUser = useCurrentUser()

// Prefixo, não igualdade: `/documents/12` mantém "Laudos" ativo.
const isActive = (path: string) => route.path === path || route.path.startsWith(`${path}/`)
const showText = computed(() => isExpanded.value || isHovered.value || isMobileOpen.value)

onMounted(() => {
  currentUser.fetchUser()
})

const menuGroups = [
  {
    titleKey: 'nav.groupMain',
    items: [
      { labelKey: 'nav.documents', path: '/documents', icon: 'mdi:file-document-multiple-outline' },
      { labelKey: 'nav.results', path: '/results', icon: 'mdi:chart-line' },
    ],
  },
  {
    titleKey: 'nav.groupAccount',
    items: [{ labelKey: 'nav.profile', path: '/profile', icon: 'mdi:account-outline' }],
  },
]
</script>

<template>
  <aside
    :class="[
      'fixed mt-16 flex flex-col lg:mt-0 top-0 px-4 left-0 bg-sepia-950 text-sepia-100 h-screen transition-all duration-300 ease-in-out z-99999 border-r border-sepia-800',
      {
        'lg:w-[290px]': isExpanded || isMobileOpen || isHovered,
        'lg:w-[90px]': !isExpanded && !isHovered,
        'translate-x-0 w-[290px]': isMobileOpen,
        '-translate-x-full': !isMobileOpen,
        'lg:translate-x-0': true,
      },
    ]"
    @mouseenter="!isExpanded && (isHovered = true)"
    @mouseleave="isHovered = false"
  >
    <div
      :class="[
        'py-6 flex border-b border-sepia-800',
        !isExpanded && !isHovered ? 'lg:justify-center' : 'justify-start',
      ]"
    >
      <NuxtLink to="/documents" class="flex items-center gap-3">
        <img src="/logo.png" :alt="$t('app.name')" class="h-8 w-auto">
        <span v-if="showText" class="font-display text-lg font-semibold text-sepia-50 whitespace-nowrap">
          {{ $t('app.name') }}
        </span>
      </NuxtLink>
    </div>

    <nav class="flex-1 overflow-y-auto p-1 pt-4 no-scrollbar">
      <div class="flex flex-col gap-5">
        <div v-for="group in menuGroups" :key="group.titleKey">
          <h2
            v-if="showText"
            class="mb-3 text-[11px] font-medium uppercase tracking-wider leading-[20px] text-sepia-400"
          >
            {{ $t(group.titleKey) }}
          </h2>
          <ul class="flex flex-col gap-1">
            <li v-for="item in group.items" :key="item.path">
              <NuxtLink
                :to="item.path"
                :aria-label="$t(item.labelKey)"
                :class="[
                  'group flex items-center gap-3 rounded-md border-l-2 px-2.5 py-2 text-sm transition-colors',
                  isActive(item.path)
                    ? 'border-sepia-300 bg-sepia-900 text-sepia-50'
                    : 'border-transparent text-sepia-300 hover:border-sepia-700 hover:bg-sepia-900/60 hover:text-sepia-50',
                ]"
              >
                <Icon
                  :icon="item.icon"
                  aria-hidden="true"
                  :class="['h-5 w-5', isActive(item.path) ? 'text-sepia-300' : 'text-sepia-500 group-hover:text-sepia-300']"
                />
                <span v-if="showText">{{ $t(item.labelKey) }}</span>
              </NuxtLink>
            </li>
          </ul>
        </div>
      </div>
    </nav>

    <div class="mt-auto border-t border-sepia-800 py-4">
      <div :class="['flex items-center gap-3', !showText ? 'lg:justify-center' : 'justify-start']">
        <!-- Só iniciais: Gravatar faria o navegador chamar um serviço de terceiros
             (com o hash do e-mail) dentro de um app de dados de saúde. -->
        <Avatar
          v-if="currentUser.user"
          :label="currentUser.initials"
          size="large"
          shape="circle"
          class="!bg-sepia-300 !text-sepia-950 !font-semibold"
        />
        <div v-if="currentUser.user && showText" class="flex flex-col">
          <span class="text-sm font-medium text-sepia-50">{{ currentUser.fullName }}</span>
          <span class="text-xs text-sepia-400">{{ currentUser.user.email }}</span>
        </div>
      </div>
    </div>
  </aside>
</template>
