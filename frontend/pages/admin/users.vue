<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import type { AdminUser, AdminUserWithSecret } from '@/types/accounts'

// Administração de contas. O administrador do app vê quem tem conta e controla o acesso; os
// exames de cada pessoa continuam privados (esta tela nem recebe contagens de laudos).
definePageMeta({ middleware: ['auth', 'admin'] })
const { t } = useI18n()
useHead({ title: () => t('admin.usersTitle') })
const fmt = useFormat()
const { get, patch, post } = useApi()
const apiError = useApiError()
const notice = useNotice()
const currentUser = useCurrentUser()

const { data: users, error, refresh, status } = await useResilientAsyncData<AdminUser[]>(
  'admin-users',
  async () => (await get<AdminUser[]>('/admin/users/')) ?? [],
  { default: () => [] },
)

const cols = useColumnVisibility(
  'admin-users',
  computed(() => [
    { key: 'name', label: t('admin.colName'), required: true },
    { key: 'email', label: t('admin.colEmail') },
    { key: 'role', label: t('admin.colRole') },
    { key: 'status', label: t('admin.colStatus') },
    { key: 'created', label: t('admin.colCreated'), mobileHidden: true },
    { key: 'lastAccess', label: t('admin.colLastAccess'), mobileHidden: true },
  ]),
)

const isSelf = (row: AdminUser) => row.id === currentUser.user?.id
const displayName = (row: AdminUser) => row.name || row.email

// --- ações com confirmação -------------------------------------------------------------------

type Kind = 'deactivate' | 'makeAdmin' | 'removeAdmin' | 'resetPassword'
const pending = ref<{ kind: Kind; user: AdminUser } | null>(null)
const confirming = computed({
  get: () => pending.value !== null,
  set: (open) => {
    if (!open && !busy.value) pending.value = null
  },
})
const busy = ref(false)

// Um `t()` literal por texto (e não uma chave montada) para o lint de i18n enxergar o uso.
const confirmTexts = computed(() => {
  if (!pending.value) return { title: '', body: '', action: '', danger: false }
  const name = displayName(pending.value.user)
  switch (pending.value.kind) {
    case 'deactivate':
      return { title: t('admin.confirmDeactivateTitle'), body: t('admin.confirmDeactivateBody', { name }), action: t('admin.actionDeactivate'), danger: true }
    case 'makeAdmin':
      return { title: t('admin.confirmMakeAdminTitle'), body: t('admin.confirmMakeAdminBody', { name }), action: t('admin.actionMakeAdmin'), danger: false }
    case 'removeAdmin':
      return { title: t('admin.confirmRemoveAdminTitle'), body: t('admin.confirmRemoveAdminBody', { name }), action: t('admin.actionRemoveAdmin'), danger: true }
    default:
      return { title: t('admin.confirmResetTitle'), body: t('admin.confirmResetBody', { name }), action: t('admin.actionResetPassword'), danger: true }
  }
})

// Senha temporária a mostrar (criação ou redefinição). Zerada quando o diálogo fecha.
const secret = ref<{ email: string; password: string } | null>(null)
const secretVisible = ref(false)
function showSecret(result: AdminUserWithSecret) {
  if (!result.temporary_password) return
  secret.value = { email: result.email, password: result.temporary_password }
  secretVisible.value = true
}

const creating = ref(false)
async function onCreated(result: AdminUserWithSecret) {
  if (result.temporary_password) showSecret(result)
  else notice.success(t('admin.createdWithPassword'))
  await refresh()
}

async function activate(row: AdminUser) {
  if (busy.value || isSelf(row)) return
  busy.value = true
  try {
    await patch(`/admin/users/${row.id}/`, { is_active: true })
    notice.success(t('admin.activated'))
  } catch (e) {
    notice.error(apiError.message(e))
  } finally {
    busy.value = false
    await refresh()
  }
}

function ask(kind: Kind, row: AdminUser) {
  if (isSelf(row)) return
  pending.value = { kind, user: row }
}

async function confirmAction() {
  const current = pending.value
  if (!current || busy.value) return
  busy.value = true
  const { kind, user } = current
  try {
    if (kind === 'resetPassword') {
      const result = await post<AdminUserWithSecret>(`/admin/users/${user.id}/reset-password/`)
      pending.value = null
      if (result) showSecret(result)
    } else {
      const changes =
        kind === 'deactivate' ? { is_active: false } : { is_admin: kind === 'makeAdmin' }
      await patch(`/admin/users/${user.id}/`, changes)
      pending.value = null
      if (kind === 'deactivate') notice.success(t('admin.deactivated'))
      else if (kind === 'makeAdmin') notice.success(t('admin.madeAdmin'))
      else notice.success(t('admin.removedAdmin'))
    }
  } catch (e) {
    // Fecha o diálogo modal: o aviso global ficaria atrás da máscara (ex.: 409 `last_admin`).
    pending.value = null
    notice.error(apiError.message(e))
  } finally {
    busy.value = false
    await refresh()
  }
}

const ACTION_BUTTON =
  'rounded p-2.5 text-sepia-500 sm:p-1.5 hover:bg-sepia-100 hover:text-sepia-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sepia-500'
const actionClass = (row: AdminUser) => [ACTION_BUTTON, isSelf(row) ? 'cursor-not-allowed opacity-40 hover:bg-transparent hover:text-sepia-500' : '']
</script>

<template>
  <div class="space-y-6">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="font-display text-2xl font-semibold text-sepia-900">{{ $t('admin.usersTitle') }}</h1>
        <p class="mt-1 max-w-2xl text-sm text-sepia-500">{{ $t('admin.usersSubtitle') }}</p>
      </div>
      <Button
        type="button"
        class="!flex !items-center !gap-1.5 !rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950"
        @click="creating = true"
      >
        <Icon icon="mdi:account-plus-outline" class="h-4 w-4" aria-hidden="true" />
        {{ $t('admin.newUser') }}
      </Button>
    </header>

    <AppLoadError v-if="error" :loading="status === 'pending'" @retry="refresh" />

    <!-- Descrição lida pelos botões desativados da própria conta. -->
    <p id="admin-self-reason" class="sr-only">{{ $t('admin.selfDisabled') }}</p>

    <section class="rounded-lg border border-sepia-200 bg-white shadow-sm">
      <div class="flex justify-end border-b border-sepia-100 p-3">
        <ColumnToggle id="admin-users" :cols="cols" />
      </div>
      <div class="relative overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-sepia-50 text-left text-xs uppercase tracking-wide text-sepia-500">
            <tr>
              <th scope="col" class="px-4 py-3">{{ $t('admin.colName') }}</th>
              <th v-if="cols.show('email')" scope="col" class="px-4 py-3">{{ $t('admin.colEmail') }}</th>
              <th v-if="cols.show('role')" scope="col" class="px-4 py-3">{{ $t('admin.colRole') }}</th>
              <th v-if="cols.show('status')" scope="col" class="px-4 py-3">{{ $t('admin.colStatus') }}</th>
              <th v-if="cols.show('created')" scope="col" class="px-4 py-3">{{ $t('admin.colCreated') }}</th>
              <th v-if="cols.show('lastAccess')" scope="col" class="px-4 py-3">{{ $t('admin.colLastAccess') }}</th>
              <th scope="col" class="px-4 py-3"><span class="sr-only">{{ $t('common.actions') }}</span></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-sepia-100">
            <tr v-if="!users.length && !error">
              <td :colspan="cols.visibleCount.value + 1" class="px-4 py-10 text-center text-sepia-500">{{ $t('admin.empty') }}</td>
            </tr>
            <tr v-for="row in users" :key="row.id" :class="['hover:bg-sepia-50', row.is_active ? '' : 'text-sepia-500']">
              <td class="px-4 py-3">
                <span class="font-medium" :class="row.is_active ? 'text-sepia-900' : ''">{{ displayName(row) }}</span>
                <span v-if="isSelf(row)" class="ml-2 rounded bg-sepia-100 px-1.5 py-0.5 text-xs text-sepia-700">{{ $t('admin.you') }}</span>
              </td>
              <td v-if="cols.show('email')" class="px-4 py-3">{{ row.email }}</td>
              <td v-if="cols.show('role')" class="px-4 py-3">
                <span
                  v-if="row.is_admin"
                  class="inline-flex items-center gap-1 rounded-full bg-sepia-900 px-2.5 py-0.5 text-xs font-medium text-sepia-50"
                >
                  <Icon icon="mdi:shield-account-outline" class="h-3.5 w-3.5" aria-hidden="true" />
                  {{ $t('admin.roleAdmin') }}
                </span>
                <span v-else class="inline-flex rounded-full bg-sepia-100 px-2.5 py-0.5 text-xs font-medium text-sepia-700">
                  {{ $t('admin.roleMember') }}
                </span>
              </td>
              <td v-if="cols.show('status')" class="px-4 py-3">
                <span
                  :class="[
                    'inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium',
                    row.is_active ? 'bg-azul-50 text-azul-800' : 'bg-vermelho-50 text-vermelho-800',
                  ]"
                >
                  {{ row.is_active ? $t('admin.statusActive') : $t('admin.statusInactive') }}
                </span>
                <p v-if="row.must_change_password" class="mt-1 text-xs text-amber-800">{{ $t('admin.passwordPending') }}</p>
              </td>
              <td v-if="cols.show('created')" class="px-4 py-3 whitespace-nowrap">{{ fmt.date(row.date_joined) }}</td>
              <td v-if="cols.show('lastAccess')" class="px-4 py-3 whitespace-nowrap">
                {{ row.last_login ? fmt.dateTime(row.last_login) : $t('admin.neverAccessed') }}
              </td>
              <td class="px-4 py-3">
                <div class="flex items-center justify-end gap-1">
                  <button
                    v-if="row.is_active"
                    type="button"
                    :class="actionClass(row)"
                    :aria-label="$t('admin.actionDeactivate')"
                    :aria-disabled="isSelf(row)"
                    :aria-describedby="isSelf(row) ? 'admin-self-reason' : undefined"
                    :title="isSelf(row) ? $t('admin.selfDisabled') : $t('admin.actionDeactivate')"
                    @click="ask('deactivate', row)"
                  >
                    <Icon icon="mdi:account-off-outline" class="h-5 w-5" aria-hidden="true" />
                  </button>
                  <button
                    v-else
                    type="button"
                    :class="actionClass(row)"
                    :aria-label="$t('admin.actionActivate')"
                    :aria-disabled="isSelf(row)"
                    :aria-describedby="isSelf(row) ? 'admin-self-reason' : undefined"
                    :title="isSelf(row) ? $t('admin.selfDisabled') : $t('admin.actionActivate')"
                    @click="activate(row)"
                  >
                    <Icon icon="mdi:account-check-outline" class="h-5 w-5" aria-hidden="true" />
                  </button>

                  <button
                    v-if="row.is_admin"
                    type="button"
                    :class="actionClass(row)"
                    :aria-label="$t('admin.actionRemoveAdmin')"
                    :aria-disabled="isSelf(row)"
                    :aria-describedby="isSelf(row) ? 'admin-self-reason' : undefined"
                    :title="isSelf(row) ? $t('admin.selfDisabled') : $t('admin.actionRemoveAdmin')"
                    @click="ask('removeAdmin', row)"
                  >
                    <Icon icon="mdi:shield-off-outline" class="h-5 w-5" aria-hidden="true" />
                  </button>
                  <button
                    v-else
                    type="button"
                    :class="actionClass(row)"
                    :aria-label="$t('admin.actionMakeAdmin')"
                    :aria-disabled="isSelf(row)"
                    :aria-describedby="isSelf(row) ? 'admin-self-reason' : undefined"
                    :title="isSelf(row) ? $t('admin.selfDisabled') : $t('admin.actionMakeAdmin')"
                    @click="ask('makeAdmin', row)"
                  >
                    <Icon icon="mdi:shield-account-outline" class="h-5 w-5" aria-hidden="true" />
                  </button>

                  <button
                    type="button"
                    :class="actionClass(row)"
                    :aria-label="$t('admin.actionResetPassword')"
                    :aria-disabled="isSelf(row)"
                    :aria-describedby="isSelf(row) ? 'admin-self-reason' : undefined"
                    :title="isSelf(row) ? $t('admin.selfDisabled') : $t('admin.actionResetPassword')"
                    @click="ask('resetPassword', row)"
                  >
                    <Icon icon="mdi:lock-reset" class="h-5 w-5" aria-hidden="true" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <p class="flex items-start gap-2 text-xs text-sepia-500">
      <Icon icon="mdi:information-outline" class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      {{ $t('admin.privacyNote') }}
    </p>

    <AdminUserCreateDialog v-model:visible="creating" @created="onCreated" />

    <AdminTemporaryPasswordDialog
      v-model:visible="secretVisible"
      :email="secret?.email ?? ''"
      :password="secret?.password ?? ''"
      @closed="secret = null"
    />

    <Dialog v-model:visible="confirming" modal :header="confirmTexts.title" class="w-[28rem] max-w-[95vw]">
      <p class="text-sm text-sepia-700">{{ confirmTexts.body }}</p>
      <div class="mt-6 flex justify-end gap-2">
        <Button
          type="button"
          :disabled="busy"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50 disabled:!opacity-50"
          @click="pending = null"
        >
          {{ $t('common.cancel') }}
        </Button>
        <Button
          type="button"
          :disabled="busy"
          :class="[
            '!rounded-md !px-4 !py-2 !text-sm !font-medium disabled:!opacity-50',
            confirmTexts.danger
              ? '!bg-vermelho-600 !text-white hover:!bg-vermelho-700'
              : '!bg-sepia-900 !text-sepia-50 hover:!bg-sepia-950',
          ]"
          @click="confirmAction"
        >
          {{ confirmTexts.action }}
        </Button>
      </div>
    </Dialog>
  </div>
</template>
