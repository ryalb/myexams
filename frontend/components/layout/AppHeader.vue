<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useSidebar } from '@/composables/useSidebar'
import { useAuth } from '@/composables/useAuth'

const { isExpanded, isMobileOpen, toggleSidebar, toggleMobileSidebar } = useSidebar()
const { logout } = useAuth()
const router = useRouter()

const handleToggle = () => {
  if (window.innerWidth >= 1024) toggleSidebar()
  else toggleMobileSidebar()
}

const handleLogout = () => {
  logout()
  router.push('/login')
}
</script>

<template>
  <header class="sticky top-0 z-[900] flex w-full border-b border-sepia-800 bg-sepia-950">
    <div class="flex items-center justify-between grow px-3 py-3 lg:px-6 lg:py-4">
      <div class="flex items-center gap-3">
        <button
          type="button"
          class="flex items-center justify-center w-10 h-10 text-sepia-300 border border-sepia-800 rounded-lg lg:w-11 lg:h-11 hover:bg-sepia-900"
          :aria-label="isMobileOpen ? $t('nav.closeMenu') : $t('nav.openMenu')"
          :aria-expanded="isExpanded || isMobileOpen"
          @click="handleToggle"
        >
          <Icon :icon="isMobileOpen ? 'mdi:close' : 'mdi:menu'" class="h-6 w-6" aria-hidden="true" />
        </button>
        <NuxtLink
          to="/documents"
          class="flex items-center gap-3 font-display text-lg font-semibold text-sepia-50"
        >
          <img src="/logo.png" :alt="$t('app.name')" class="h-8 w-auto">
          <span class="hidden sm:inline">{{ $t('app.name') }}</span>
        </NuxtLink>
      </div>

      <button
        type="button"
        class="flex h-10 items-center gap-2 rounded-lg px-2.5 text-sepia-300 hover:bg-sepia-900 sm:px-3"
        :aria-label="$t('nav.logout')"
        @click="handleLogout"
      >
        <Icon icon="mdi:logout" class="h-5 w-5" aria-hidden="true" />
        <span class="hidden sm:inline">{{ $t('nav.logout') }}</span>
      </button>
    </div>
  </header>
</template>
