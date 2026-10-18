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
  <header class="sticky top-0 flex w-full bg-sepia-950 border-sepia-800 z-99999 border-b">
    <div class="flex items-center justify-between grow px-3 py-3 lg:px-6 lg:py-4">
      <div class="flex items-center gap-3">
        <button
          type="button"
          class="flex items-center justify-center w-10 h-10 text-sepia-300 border border-sepia-800 rounded-lg lg:w-11 lg:h-11 hover:bg-sepia-900"
          :aria-label="$t('nav.openMenu')"
          :aria-expanded="isExpanded || isMobileOpen"
          @click="handleToggle"
        >
          <Icon icon="mdi:menu" class="h-6 w-6" aria-hidden="true" />
        </button>
        <NuxtLink
          to="/documents"
          class="hidden lg:flex items-center gap-3 text-lg font-semibold text-sepia-50 font-display"
        >
          <img src="/logo.png" :alt="$t('app.name')" class="h-8 w-auto">
          <span>{{ $t('app.name') }}</span>
        </NuxtLink>
      </div>

      <button
        type="button"
        class="flex items-center gap-2 px-3 py-2 text-sepia-300 rounded-lg hover:bg-sepia-900"
        @click="handleLogout"
      >
        <Icon icon="mdi:logout" class="h-5 w-5" aria-hidden="true" />
        <span>{{ $t('nav.logout') }}</span>
      </button>
    </div>
  </header>
</template>
