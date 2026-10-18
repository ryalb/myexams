// Formatos pt-BR compartilhados: `$d(valor, 'short')` -> 25/08/2026,
// `$n(valor, 'decimal')` -> 1.234,5. Usados também nos eixos e tooltips do Chart.js.
export default defineI18nConfig(() => ({
  legacy: false,
  locale: 'pt-BR',
  fallbackLocale: 'pt-BR',
  numberFormats: {
    'pt-BR': {
      decimal: { style: 'decimal', maximumFractionDigits: 3 },
      integer: { style: 'decimal', maximumFractionDigits: 0 },
      fixed1: { style: 'decimal', minimumFractionDigits: 1, maximumFractionDigits: 1 },
      percent: { style: 'decimal', maximumFractionDigits: 1 },
    },
  },
  datetimeFormats: {
    'pt-BR': {
      // Carimbos de data dos laudos estão em horário de Brasília: sem fixar o fuso,
      // quem abre o app de outro fuso veria o dia anterior/seguinte.
      short: { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'America/Sao_Paulo' },
      monthYear: { year: 'numeric', month: 'short' },
      year: { year: 'numeric' },
      long: {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'America/Sao_Paulo',
      },
    },
  },
}))
