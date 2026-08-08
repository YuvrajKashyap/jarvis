export type PairingLink = {
  pairingId: string;
  secret: string;
};

export type PhoneSession = {
  token: string;
  deviceId: string;
  expiresAt: string;
};

type StoredCredential = {
  id: "primary";
  deviceId: string;
  privateKey: CryptoKey;
};

const DATABASE_NAME = "jarvis-auth";
const STORE_NAME = "credentials";

export class UnpairedPhoneError extends Error {}

export function parsePairingFragment(fragment: string): PairingLink | null {
  const encoded = new URLSearchParams(fragment.replace(/^#/, "")).get("pair");
  if (!encoded) return null;
  try {
    const parsed: unknown = JSON.parse(decodeBase64UrlText(encoded));
    if (!isRecord(parsed)) return null;
    const pairingId = parsed.pairingId;
    const secret = parsed.secret;
    if (
      typeof pairingId !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        pairingId,
      ) ||
      typeof secret !== "string" ||
      secret.length < 32 ||
      secret.length > 512
    ) {
      return null;
    }
    return { pairingId, secret };
  } catch {
    return null;
  }
}

export async function authenticatePhone(baseUrl: string): Promise<PhoneSession> {
  const offer = parsePairingFragment(window.location.hash);
  let credential = await loadCredential();
  if (offer) {
    credential = await pairPhone(baseUrl, offer);
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
  if (!credential) throw new UnpairedPhoneError("This iPhone has not been paired with JARVIS.");

  const challenge = await requestJson(`${baseUrl}/v1/auth/challenges`, {
    device_id: credential.deviceId,
  });
  if (!isRecord(challenge) || typeof challenge.challenge_id !== "string") {
    throw new Error("JARVIS returned an invalid phone challenge.");
  }
  if (typeof challenge.challenge !== "string") {
    throw new Error("JARVIS returned an invalid phone challenge.");
  }
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    credential.privateKey,
    decodeBase64Url(challenge.challenge),
  );
  const session = await requestJson(`${baseUrl}/v1/auth/sessions`, {
    challenge_id: challenge.challenge_id,
    signature: encodeBase64Url(new Uint8Array(signature)),
  });
  if (
    !isRecord(session) ||
    typeof session.token !== "string" ||
    typeof session.device_id !== "string" ||
    typeof session.expires_at !== "string"
  ) {
    throw new Error("JARVIS returned an invalid phone session.");
  }
  return {
    token: session.token,
    deviceId: session.device_id,
    expiresAt: session.expires_at,
  };
}

async function pairPhone(baseUrl: string, offer: PairingLink): Promise<StoredCredential> {
  const keyPair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, [
    "sign",
    "verify",
  ]);
  const exported = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
  if (!exported.x || !exported.y) throw new Error("Could not create the iPhone identity.");
  const deviceId = `iphone-${crypto.randomUUID()}`;
  await requestJson(`${baseUrl}/v1/pairing/${offer.pairingId}/complete`, {
    secret: offer.secret,
    device_id: deviceId,
    public_key_jwk: { kty: "EC", crv: "P-256", x: exported.x, y: exported.y },
  });
  const credential: StoredCredential = { id: "primary", deviceId, privateKey: keyPair.privateKey };
  await saveCredential(credential);
  return credential;
}

async function requestJson(url: string, body: Record<string, unknown>): Promise<unknown> {
  const response = await fetch(url, {
    method: "POST",
    cache: "no-store",
    credentials: "omit",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`JARVIS request failed (${response.status}).`);
  return response.json();
}

async function loadCredential(): Promise<StoredCredential | null> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = database
      .transaction(STORE_NAME, "readonly")
      .objectStore(STORE_NAME)
      .get("primary");
    request.onsuccess = () => resolve((request.result as StoredCredential | undefined) ?? null);
    request.onerror = () => reject(request.error);
  });
}

async function saveCredential(credential: StoredCredential): Promise<void> {
  const database = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(credential);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

async function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME, { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function decodeBase64UrlText(value: string): string {
  return new TextDecoder().decode(decodeBase64Url(value));
}

function decodeBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const binary = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function encodeBase64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
