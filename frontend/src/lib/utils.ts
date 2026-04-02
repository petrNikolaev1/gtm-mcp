export function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(' ')
}
export function formatNumber(n: number) {
  return new Intl.NumberFormat().format(n)
}
export function getErrorMessage(e: any): string {
  return e?.message || e?.detail || String(e)
}
