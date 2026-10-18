import { Icon, addCollection } from '@iconify/vue'
import mdiSubset from '@/assets/icons/mdi-subset.json'

// Coleção embutida (gerada por `bun run icons`): sem chamadas a api.iconify.design,
// então os ícones funcionam offline e nenhum pedido de terceiros sai do app.
export default defineNuxtPlugin((nuxtApp) => {
  addCollection(mdiSubset)
  nuxtApp.vueApp.component('Icon', Icon)
})
