<template>
  <div
    v-if="isMobileOpen"
    class="fixed inset-x-0 bottom-0 top-16 z-[910] bg-sepia-900/50 lg:hidden print:hidden"
    @click="toggleMobileSidebar"
  />
</template>

<script setup lang="ts">
import { useSidebar } from '@/composables/useSidebar'
const { toggleMobileSidebar, isMobileOpen } = useSidebar()
</script>
