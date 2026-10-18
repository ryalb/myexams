import Aura from '@primeuix/themes/aura'
import { definePreset } from '@primeuix/themes'
import PrimeVue from 'primevue/config'

// Mesma escala do `sepia` em tailwind.config.ts. O Aura usa `primary` para
// preencher botão, foco e seleção; sem sobrescrever, tudo sai no emerald dele.
const SEPIA = {
  50: '#fbf7ee',
  100: '#f5eedd',
  200: '#e7d9b8',
  300: '#cfb683',
  400: '#ad8a54',
  500: '#85652f',
  600: '#6b4f26',
  700: '#4e3a1d',
  800: '#33261a',
  900: '#241b10',
  950: '#160f09',
}

const SepiaAura = definePreset(Aura, {
  semantic: {
    primary: SEPIA,
    overlay: {
      popover: {
        background: '#ffffff',
        borderColor: SEPIA[200],
        color: SEPIA[800],
        borderRadius: '0.5rem',
        shadow: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
      },
    },
  },
})

// Nomes de meses/dias vêm do próprio `Intl` pt-BR; as frases vêm do pt-BR.json
// (grupo `primevue`), a mesma fonte do resto da interface.
const intlNames = (options: Intl.DateTimeFormatOptions, count: number, start: (i: number) => Date) => {
  const format = new Intl.DateTimeFormat('pt-BR', options)
  return Array.from({ length: count }, (_, i) => format.format(start(i)))
}

export default defineNuxtPlugin({
  name: 'primevue-locale',
  dependsOn: ['i18n:plugin'],
  setup(nuxtApp) {
    const t = nuxtApp.$i18n.t
    const text = (key: string) => t(`primevue.${key}`)
    // 2023-01-01 foi um domingo.
    const sunday = (i: number) => new Date(2023, 0, 1 + i)

    nuxtApp.vueApp.use(PrimeVue, {
      locale: {
        firstDayOfWeek: 0,
        dateFormat: text('dateFormat'),
        today: text('today'),
        clear: text('clear'),
        weekHeader: text('weekHeader'),
        chooseYear: text('chooseYear'),
        chooseMonth: text('chooseMonth'),
        chooseDate: text('chooseDate'),
        prevMonth: text('prevMonth'),
        nextMonth: text('nextMonth'),
        prevYear: text('prevYear'),
        nextYear: text('nextYear'),
        monthNames: intlNames({ month: 'long' }, 12, (i) => new Date(2023, i, 1)),
        monthNamesShort: intlNames({ month: 'short' }, 12, (i) => new Date(2023, i, 1)),
        dayNames: intlNames({ weekday: 'long' }, 7, sunday),
        dayNamesShort: intlNames({ weekday: 'short' }, 7, sunday),
        dayNamesMin: intlNames({ weekday: 'narrow' }, 7, sunday),
        emptyFilterMessage: text('emptyFilterMessage'),
        emptySearchMessage: text('emptySearchMessage'),
        emptySelectionMessage: text('emptySelectionMessage'),
        searchMessage: text('searchMessage'),
        selectionMessage: text('selectionMessage'),
        emptyMessage: text('emptyMessage'),
        prevDecade: text('prevDecade'),
        nextDecade: text('nextDecade'),
        aria: {
          close: text('ariaClose'),
          previous: text('ariaPrevious'),
          next: text('ariaNext'),
          navigation: text('ariaNavigation'),
          listLabel: text('ariaListLabel'),
        },
      },
      theme: {
        preset: SepiaAura,
        // `darkModeSelector` default é "system": o SO escuro fazia o navegador
        // desenhar inputs nativos pretos numa página clara. O app é claro.
        options: { darkModeSelector: false },
      },
    })
  },
})
