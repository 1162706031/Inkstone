import type { components } from "./generated/schema";

export type Contract = components["schemas"];

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) { super(message); }
}

export async function api<T>(path: string, method = "GET", body?: unknown, extra?: HeadersInit): Promise<T> {
  const headers = new Headers(extra);
  if (method !== "GET") {
    const csrf = await api<Contract["CSRFView"]>("/auth/csrf");
    headers.set("X-CSRF-Token", csrf.csrf_token);
  }
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.error?.code ?? "HTTP_ERROR", body?.error?.message ?? `请求失败 (${response.status})`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
