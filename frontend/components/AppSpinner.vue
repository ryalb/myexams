<script setup lang="ts">
// `color` padrao `currentColor` em vez do `black` do ldrs: herda a cor do
// texto em volta (botao, pagina), igual aos icones do app.
withDefaults(
  defineProps<{ size?: number | string; speed?: number | string; color?: string }>(),
  { size: 45, speed: 1.75, color: 'currentColor' },
)
</script>

<template>
  <l-quantum :size="size" :speed="speed" :color="color" />
</template>
