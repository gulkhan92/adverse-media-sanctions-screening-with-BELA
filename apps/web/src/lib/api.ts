/** Same-origin API client. Requests go through the /api rewrite configured in next.config.ts. */

export interface Problem {
  title?: string;
  detail?: string;
  type?: string;
  errors?: { loc?: (string | number)[]; msg?: string }[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public problem: Problem,
  ) {
    super(problem.detail ?? problem.title ?? `Request failed with status ${status}`);
  }
}

export async function post<T = unknown>(
  path: string,
  body?: unknown,
  query?: Record<string, string>,
) {
  const qs = query ? `?${new URLSearchParams(query).toString()}` : "";
  const res = await fetch(`/api/v1${path}${qs}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? (JSON.parse(text) as unknown) : {};
  if (!res.ok) throw new ApiError(res.status, data as Problem);
  return data as T;
}

export const messageOf = (error: unknown): string =>
  error instanceof ApiError || error instanceof Error
    ? error.message
    : "The request could not be completed. Try again.";
