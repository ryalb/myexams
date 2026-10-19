// Limite de tamanho do upload. A plataforma (Vercel) corta o corpo da requisição antes de o
// backend responder `file_too_large`; por isso o limite é conferido aqui, antes de enviar.

export function uploadLimitBytes(maxMb: number | null | undefined): number | null {
  return typeof maxMb === 'number' && maxMb > 0 ? maxMb * 1024 * 1024 : null
}

export function exceedsUploadLimit(sizeBytes: number, maxMb: number | null | undefined): boolean {
  const limit = uploadLimitBytes(maxMb)
  return limit !== null && sizeBytes > limit
}
