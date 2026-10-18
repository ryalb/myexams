import { quantum } from 'ldrs'

// Spinner do app (https://uiball.com/ldrs/) -- web component, so existe no
// navegador; usado via `components/AppSpinner.vue`.
export default defineNuxtPlugin(() => {
  quantum.register()
})
