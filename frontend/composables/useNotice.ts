// Aviso curto (sucesso/erro) mostrado no layout. O texto já chega traduzido.
interface Notice {
  kind: 'success' | 'error'
  text: string
}

// No módulo, não por chamada: cada componente chama `useNotice()` e teria o seu
// próprio timer; o de um aviso antigo dispensaria o aviso novo.
let timer: ReturnType<typeof setTimeout> | undefined

export function useNotice() {
  const notice = useState<Notice | null>('notice', () => null)

  const show = (kind: Notice['kind'], text: string) => {
    notice.value = { kind, text }
    clearTimeout(timer)
    timer = setTimeout(() => (notice.value = null), 6000)
  }

  const dismiss = () => {
    clearTimeout(timer)
    notice.value = null
  }

  return {
    notice,
    success: (text: string) => show('success', text),
    error: (text: string) => show('error', text),
    dismiss,
  }
}
