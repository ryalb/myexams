<script setup lang="ts">
const { t } = useI18n()

// "Laudos · MyExams"; sem título de página, só o nome do app.
useHead({
  titleTemplate: (title) => (title && title !== t('app.name') ? `${title} · ${t('app.name')}` : t('app.name')),
})
</script>

<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
