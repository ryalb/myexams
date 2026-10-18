// Conta vista pelo administrador (`/api/v1/admin/users/`). Só dados da conta: o backend nunca
// devolve contagem ou conteúdo de exames aqui.
export interface AdminUser {
  id: number
  email: string
  // Nome de exibição; vazio quando a conta foi criada sem nome.
  name: string
  is_active: boolean
  is_admin: boolean
  // Senha temporária ainda não trocada.
  must_change_password: boolean
  date_joined: string
  last_login: string | null
}

// Resposta de criar/redefinir: a senha temporária vem uma única vez (null se o administrador
// digitou a senha).
export interface AdminUserWithSecret extends AdminUser {
  temporary_password: string | null
}

// `type` (e não `interface`): precisa ser atribuível ao corpo JSON de `useApi().post`.
export type AdminUserInput = {
  email: string
  name: string
  is_admin: boolean
  // Ausente = o servidor gera uma senha temporária.
  password?: string
}
