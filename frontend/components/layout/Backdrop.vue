<template>
  <div
    v-if="isMobileOpen"
    class="fixed inset-0 bg-sepia-900/50 z-9999 lg:hidden print:hidden"
    @click="toggleMobileSidebar"
  />
</template>

<script setup lang="ts">
import { useSidebar } from '@/composables/useSidebar'
const { toggleMobileSidebar, isMobileOpen } = useSidebar()
</script>
