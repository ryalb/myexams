#!/usr/bin/env node
/**
 * Gera `assets/icons/mdi-subset.json` só com os ícones `mdi:*` usados no código.
 *
 * O `@iconify/vue` busca ícones em api.iconify.design em tempo de execução. Para um
 * app de dados de saúde isso é uma chamada de terceiros desnecessária e quebra o uso
 * offline. Registrar esta coleção no plugin (`plugins/iconify.ts`) elimina a rede.
 *
 * Rode `bun run icons` ao adicionar um ícone novo; `bun run lint` confere o arquivo.
 */
import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = new URL('..', import.meta.url).pathname
const SKIP = new Set(['node_modules', '.nuxt', '.output', 'dist', 'assets'])
const OUT = join(ROOT, 'assets/icons/mdi-subset.json')

const walk = (dir) =>
  readdirSync(dir).flatMap((entry) => {
    if (SKIP.has(entry)) return []
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return /\.(vue|ts)$/.test(full) ? [full] : []
  })

const used = new Set()
for (const file of walk(ROOT)) {
  for (const match of readFileSync(file, 'utf8').matchAll(/mdi:([a-z0-9-]+)/g)) used.add(match[1])
}

const full = JSON.parse(readFileSync(join(ROOT, 'node_modules/@iconify-json/mdi/icons.json'), 'utf8'))
const subset = { prefix: 'mdi', width: full.width, height: full.height, icons: {} }
const missing = []
for (const name of [...used].sort()) {
  if (full.icons[name]) subset.icons[name] = full.icons[name]
  else if (full.aliases?.[name]) {
    const parent = full.aliases[name].parent
    subset.icons[parent] = full.icons[parent]
    subset.aliases = { ...subset.aliases, [name]: full.aliases[name] }
  } else missing.push(name)
}

if (missing.length) {
  console.error(`Ícones inexistentes na coleção mdi: ${missing.join(', ')}`)
  process.exit(1)
}

const next = `${JSON.stringify(subset, null, 2)}\n`
if (process.argv.includes('--check')) {
  let current = ''
  try { current = readFileSync(OUT, 'utf8') } catch { /* ausente */ }
  if (current !== next) {
    console.error('assets/icons/mdi-subset.json desatualizado. Rode: bun run icons')
    process.exit(1)
  }
  console.log(`build-icons: ${used.size} ícones em dia.`)
} else {
  mkdirSync(join(ROOT, 'assets/icons'), { recursive: true })
  writeFileSync(OUT, next)
  console.log(`build-icons: ${used.size} ícones gravados em assets/icons/mdi-subset.json`)
}
