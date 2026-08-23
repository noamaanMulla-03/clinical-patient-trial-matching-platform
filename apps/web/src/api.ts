export type ApiError = {
  detail?: { message?: string };
};

const apiBase = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/$/, '');

export function apiPath(path: string): string {
  return `${apiBase}${path}`;
}

export async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiPath(path), {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });

  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.detail?.message ?? 'The request could not be completed.');
  }
  return (await response.json()) as T;
}
