// Reuse the existing organization setting; historical document snapshots stay unchanged.
export const PRODUCT_NAME = 'OrgProekt'
export function workspaceName(organization?: string | null) {
  const name = organization?.trim()
  return !name || name === 'Название организации' ? PRODUCT_NAME : name
}
