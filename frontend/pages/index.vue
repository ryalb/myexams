<script setup lang="ts">
definePageMeta({ layout: false, middleware: 'auth' })
await navigateTo('/documents', { replace: true })
</script>

<template>
  <div />
</template>
