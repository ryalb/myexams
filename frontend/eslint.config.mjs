import { createConfigForNuxt } from '@nuxt/eslint-config'
import vueI18n from '@intlify/eslint-plugin-vue-i18n'

// Grupos cujas chaves são montadas em tempo de execução a partir de códigos do
// backend (`$t('status.' + code)`); `check-i18n` (backend) garante a paridade.
const DYNAMIC_GROUPS = '/^(status|runStatus|flag|reviewStatus|origin|category|sex|method|errors|warnings)\\./'
// Chaves escolhidas por dados (itens de menu, rótulos de campos, faixas de data e
// o locale do PrimeVue) e portanto invisíveis à análise estática.
const DATA_DRIVEN = '/^(nav|primevue)\\.|^auth\\.(coverPoint|firstName|lastName)|^analyte\\.range|^document\\.diag(Kind|Outcome)\\./'

export default createConfigForNuxt().append(
  ...vueI18n.configs['flat/recommended'],
  {
    settings: {
      'vue-i18n': {
        localeDir: './locales/*.json',
        messageSyntaxVersion: '^9.0.0',
      },
    },
    rules: {
      // Nada de texto cru nos templates: tudo passa por `$t(...)`.
      '@intlify/vue-i18n/no-raw-text': [
        'error',
        {
          ignoreNodes: ['Icon', 'l-quantum'],
          ignorePattern: '^[-#:()&+=—–·•/%,.…*\\s\\d]+$',
        },
      ],
      '@intlify/vue-i18n/no-missing-keys': 'error',
      '@intlify/vue-i18n/no-unused-keys': [
        'error',
        { src: '.', extensions: ['.vue', '.ts'], ignores: [DYNAMIC_GROUPS, DATA_DRIVEN] },
      ],
      // O próprio vue-i18n valida o formato das mensagens.
      '@intlify/vue-i18n/valid-message-syntax': 'error',
    },
  },
  { ignores: ['.nuxt/**', '.output/**', 'node_modules/**'] },
)
