#!/usr/bin/env node
/**
 * Falha se algum <Button> do PrimeVue não definir fundo no estado normal.
 *
 * O `<Button>` do PrimeVue é preenchido por padrão, com a cor `primary` do preset.
 * Um botão estilizado só com borda e cor de texto (padrão de contorno usado aqui)
 * herda esse fundo escuro e fica texto-escuro-sobre-escuro.
 *
 * Existe como script porque duas auditorias por grep já falharam: `grep -v '!bg-'`
 * descarta justamente o caso problemático, o botão que tem `hover:!bg-...` — fundo
 * apenas no hover significa que o estado normal cai no fill default.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = new URL('..', import.meta.url).pathname
const SKIP = new Set(['node_modules', '.nuxt', '.output', 'dist'])

const walk = (dir) =>
  readdirSync(dir).flatMap((entry) => {
    if (SKIP.has(entry)) return []
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return full.endsWith('.vue') ? [full] : []
  })

// Fundo condicional não conta: só vale no estado que o modificador descreve.
const VARIANT_BG = /(?:hover|focus|active|disabled|group-hover|dark):!?bg-[\w./[\]-]+/g
const BASE_BG = /(?<![\w:-])!?bg-[\w./[\]-]+/

const offenders = []
for (const file of walk(ROOT)) {
  const source = readFileSync(file, 'utf8')
  for (const match of source.matchAll(/<Button\b(.*?)>/gs)) {
    const attrs = match[1]
    // `text` / `link` são variantes sem preenchimento por definição.
    if (/\s(?:text|link)(?=[\s\n/>])/.test(attrs)) continue
    if (BASE_BG.test(attrs.replace(VARIANT_BG, ''))) continue
    offenders.push(`${relative(ROOT, file)}:${source.slice(0, match.index).split('\n').length}`)
  }
}

if (offenders.length) {
  console.error('<Button> sem fundo no estado normal (herdaria o fill do PrimeVue):')
  for (const spot of offenders) console.error(`  - ${spot}`)
  console.error('\nAdicione um !bg-* explícito, ou use a prop `text` para botão plano.')
  process.exit(1)
}

console.log('audit-buttons: todos os <Button> definem fundo no estado normal.')
