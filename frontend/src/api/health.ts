import type { paths } from "./generated/schema";

const path = "/api/health" satisfies keyof paths;
type Health = paths[typeof path]["get"]["responses"][200]["content"]["application/json"];

export async function getHealth(request: typeof fetch = fetch): Promise<Health> {
  const response = await request(path);
  if (!response.ok) {
    throw new Error(`Health request failed (${response.status})`);
  }
  return response.json();
}
