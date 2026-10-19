export default defineNuxtConfig({
  devtools: { enabled: true },
  compatibilityDate: '2026-08-07',
  css: ['~/assets/css/tailwind.css', '~/assets/css/theme.css', 'primeicons/primeicons.css'],
  // `@nuxtjs/apollo` fica de propósito (decisão do dono do projeto): as consultas
  // GraphQL passam por `useApi().graphql`, como na base `myfinances`.
  modules: ['@nuxtjs/tailwindcss', '@nuxtjs/apollo', '@nuxtjs/i18n'],
  ssr: false,
  // Web components do ldrs (`<l-quantum>`): sem isso o Vue tenta resolver a tag
  // como componente e avisa no console.
  vue: {
    compilerOptions: {
      isCustomElement: (tag) => tag.startsWith('l-'),
    },
  },
  // Um único idioma (pt-BR) e rotas sem prefixo, como no `finances`. Todo texto
  // visível mora em `locales/pt-BR.json`; o backend só devolve códigos.
  i18n: {
    strategy: 'no_prefix',
    defaultLocale: 'pt-BR',
    restructureDir: false,
    langDir: 'locales',
    vueI18n: './i18n.config.ts',
    locales: [{ code: 'pt-BR', language: 'pt-BR', name: 'Português (Brasil)', file: 'pt-BR.json' }],
    detectBrowserLanguage: false,
    bundle: { optimizeTranslationDirective: false },
  },
  app: {
    head: {
      htmlAttrs: { lang: 'pt-BR' },
      title: 'MyExams',
      link: [{ rel: 'icon', type: 'image/png', href: '/logo.png' }],
    },
  },
  runtimeConfig: {
    public: {
      apiBaseUrl: process.env.NUXT_PUBLIC_API_BASE_URL || 'http://localhost:8100/api/v1',
      graphqlEndpoint: process.env.NUXT_PUBLIC_GRAPHQL_ENDPOINT || 'http://localhost:8100/graphql/',
      // Tempo real (WebSocket). Ligado por padrão (desenvolvimento). Na nuvem não há WebSocket:
      // `NUXT_PUBLIC_REALTIME=false` manda o app direto para o polling, sem socket nem retentativas.
      realtime: process.env.NUXT_PUBLIC_REALTIME !== 'false',
      // Base do WebSocket (ex.: wss://api.exemplo.com.br). Vazia = derivada de `apiBaseUrl`.
      wsBaseUrl: process.env.NUXT_PUBLIC_WS_BASE_URL || '',
      // Limite de upload (MB) quando a API ainda não informou o dela (`/auth/me/` traz `limits`).
      // 0 = desconhecido: o backend decide.
      maxUploadMb: Number(process.env.NUXT_PUBLIC_MAX_UPLOAD_MB) || 0,
    },
  },
  apollo: {
    clients: {
      default: {
        httpEndpoint: process.env.NUXT_PUBLIC_GRAPHQL_ENDPOINT || 'http://localhost:8100/graphql/',
      },
    },
  },
})
