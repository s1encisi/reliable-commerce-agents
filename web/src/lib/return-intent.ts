/** 只持久化操作 ID 与载荷指纹，绝不保存退货原因。 */
const prefix = "return-intent:v1:";
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function key(owner: string, orderId: string): string {
  return `${prefix}${owner}:${orderId}`;
}

function read(owner: string, orderId: string): { id: string; fingerprint: string } | null {
  try {
    const stored = JSON.parse(localStorage.getItem(key(owner, orderId)) ?? "null");
    return stored && uuidPattern.test(stored.id) && typeof stored.fingerprint === "string" ? stored : null;
  } catch {
    return null;
  }
}

export function currentReturnIntent(owner: string, orderId: string): string | null {
  return read(owner, orderId)?.id ?? null;
}

export function clearReturnIntent(owner: string, orderId: string): void {
  localStorage.removeItem(key(owner, orderId));
}

export async function getReturnIntent(owner: string, orderId: string, reason: string, method: string): Promise<string> {
  const input = new TextEncoder().encode(JSON.stringify([orderId, reason.trim(), method]));
  const digest = await crypto.subtle.digest("SHA-256", input);
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const existing = read(owner, orderId);
  if (existing?.fingerprint === fingerprint) return existing.id;
  const id = crypto.randomUUID();
  // 在发送之前先保存。存储失败绝不能导致发出一个无法追踪的请求。
  localStorage.setItem(key(owner, orderId), JSON.stringify({ id, fingerprint }));
  return id;
}
