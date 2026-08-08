export type DesktopPairingOffer = {
  pairingId: string;
  expiresAt: string;
  pairingUrl: string;
};

type Fetcher = (input: string, init: RequestInit) => Promise<Response>;

export async function createDesktopPairingOffer(
  desktopToken: string,
  fetcher: Fetcher = fetch,
): Promise<DesktopPairingOffer> {
  const response = await fetcher("http://127.0.0.1:7331/v1/pairing/offers", {
    method: "POST",
    cache: "no-store",
    credentials: "omit",
    headers: { Authorization: `Bearer ${desktopToken}` },
  });
  if (!response.ok) throw new Error(`JARVIS pairing request failed (${response.status}).`);
  const body: unknown = await response.json();
  if (!isRecord(body) || body.pairing_url === null) {
    throw new Error("Tailscale Serve is not configured for this JARVIS host.");
  }
  if (
    typeof body.pairing_id !== "string" ||
    typeof body.expires_at !== "string" ||
    typeof body.pairing_url !== "string"
  ) {
    throw new Error("JARVIS returned an invalid pairing offer.");
  }
  const pairingUrl = new URL(body.pairing_url);
  if (
    pairingUrl.protocol !== "https:" ||
    pairingUrl.search ||
    !pairingUrl.hash.startsWith("#pair=")
  ) {
    throw new Error("JARVIS returned an unsafe pairing URL.");
  }
  return {
    pairingId: body.pairing_id,
    expiresAt: body.expires_at,
    pairingUrl: pairingUrl.toString(),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
