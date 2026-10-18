import type { Config } from "tailwindcss"

export default {
  content: [
    "./components/**/*.{js,vue,ts}",
    "./layouts/**/*.vue",
    "./pages/**/*.vue",
    "./plugins/**/*.{js,ts}",
    "./app.vue"
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Iosevka Nerd Font"', 'Iosevka', 'ui-monospace', 'monospace'],
        display: ['"Iosevka Nerd Font"', 'Iosevka', 'ui-monospace', 'monospace'],
        mono: ['"Space Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // Ramo neutro "papel → tinta": mesmos tons usados como fundo de página
        // (extremo claro) e texto/capa escura (extremo escuro) — a tinta do
        // rodapé é a mesma tinta do texto, só que aplicada como fundo.
        brand: {
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
        },
        sepia: {
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
        },
        // "No azul" — convenção do extrato bancário brasileiro para saldo
        // positivo. Substitui o emerald genérico nos valores financeiros.
        azul: {
          50: '#eaf1f7',
          100: '#cfe1ee',
          200: '#a3c6de',
          300: '#74a8cb',
          400: '#4d8ab5',
          500: '#2e6c99',
          600: '#235777',
          700: '#1c4560',
          800: '#16354a',
          900: '#102538',
        },
        // "No vermelho" — mesma convenção para saldo negativo. Substitui o
        // rose genérico.
        vermelho: {
          50: '#fbeded',
          100: '#f5d2cf',
          200: '#e8a6a0',
          300: '#d97b72',
          400: '#c25850',
          500: '#a5423a',
          600: '#8c3331',
          700: '#712a28',
          800: '#571f1e',
          900: '#3d1615',
        },
      },
      boxShadow: {
        'theme-sm': '0px 1px 3px 0px rgba(16, 24, 40, 0.1), 0px 1px 2px 0px rgba(16, 24, 40, 0.06)',
        'theme-md': '0px 4px 8px -2px rgba(16, 24, 40, 0.1), 0px 2px 4px -2px rgba(16, 24, 40, 0.06)',
        'theme-lg': '0px 12px 16px -4px rgba(16, 24, 40, 0.08), 0px 4px 6px -2px rgba(16, 24, 40, 0.03)',
        'theme-xl': '0px 20px 24px -4px rgba(16, 24, 40, 0.08), 0px 8px 8px -4px rgba(16, 24, 40, 0.03)',
      },
      zIndex: {
        '1': 1,
        '9': 9,
        '99': 99,
        '999': 999,
        '9999': 9999,
        '99999': 99999,
      },
      screens: {
        '2xsm': '375px',
        'xsm': '425px',
        'sm': '640px',
        'md': '768px',
        'lg': '1024px',
        'xl': '1280px',
        '2xl': '1536px',
        '3xl': '2000px',
      }
    }
  },
  plugins: []
} satisfies Config
