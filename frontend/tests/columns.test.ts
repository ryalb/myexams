// `bun test`: escolha de colunas visíveis das tabelas.
import { describe, expect, test } from 'bun:test'
import { defaultHidden, parseHidden, toggleHidden, type ColumnDef } from '../utils/columns'

const columns: ColumnDef[] = [
  { key: 'name', label: 'Exame', required: true, mobileHidden: true },
  { key: 'value', label: 'Valor' },
  { key: 'reference', label: 'Referência', mobileHidden: true },
  { key: 'origin', label: 'Origem', mobileHidden: true },
]

describe('defaultHidden', () => {
  test('no desktop nada começa escondido', () => {
    expect(defaultHidden(columns, false)).toEqual([])
  })

  test('no celular esconde só as marcadas e nunca a obrigatória', () => {
    expect(defaultHidden(columns, true)).toEqual(['reference', 'origin'])
  })
})

describe('parseHidden', () => {
  test('sem nada salvo usa o padrão', () => {
    expect(parseHidden(null, columns)).toBeNull()
    expect(parseHidden('', columns)).toBeNull()
  })

  test('texto corrompido ou de outro formato usa o padrão', () => {
    expect(parseHidden('{oops', columns)).toBeNull()
    expect(parseHidden('{"reference":true}', columns)).toBeNull()
  })

  test('descarta chaves desconhecidas, não textuais, repetidas e a coluna obrigatória', () => {
    expect(parseHidden('["reference","gone",1,"name","reference"]', columns)).toEqual(['reference'])
  })

  test('lista vazia salva é uma escolha válida (mostrar tudo)', () => {
    expect(parseHidden('[]', columns)).toEqual([])
  })
})

describe('toggleHidden', () => {
  test('esconde e volta a mostrar', () => {
    const hidden = toggleHidden([], 'value', columns)
    expect(hidden).toEqual(['value'])
    expect(toggleHidden(hidden, 'value', columns)).toEqual([])
  })

  test('não esconde a coluna obrigatória nem uma desconhecida', () => {
    expect(toggleHidden(['value'], 'name', columns)).toEqual(['value'])
    expect(toggleHidden(['value'], 'nope', columns)).toEqual(['value'])
  })

  test('não altera a lista recebida', () => {
    const hidden = ['value']
    toggleHidden(hidden, 'origin', columns)
    expect(hidden).toEqual(['value'])
  })
})
