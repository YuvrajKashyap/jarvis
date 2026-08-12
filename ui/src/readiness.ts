import type { ReadinessSnapshot } from "./generated";
import generatedReadinessValidator from "./generated/readiness-snapshot.validator";

type ReadinessValidator = (candidate: unknown) => candidate is ReadinessSnapshot;
type Request = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

const validateReadiness = generatedReadinessValidator as unknown as ReadinessValidator;

export async function readReadiness(
  baseUrl: string,
  token: string,
  request: Request = fetch,
): Promise<ReadinessSnapshot> {
  const timeout = AbortSignal.timeout(5_000);
  const response = await request(`${baseUrl}/v1/diagnostics`, {
    cache: "no-store",
    credentials: "omit",
    headers: { Authorization: `Bearer ${token}` },
    signal: timeout,
  });
  if (!response.ok) throw new Error(`readiness request failed (${response.status})`);

  const candidate: unknown = await response.json();
  if (!validateReadiness(candidate)) throw new Error("invalid readiness response");
  return candidate;
}
