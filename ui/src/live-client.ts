import type { ClientEvent } from "./generated";
import type { OverlayView } from "./overlay";
import { parseServerEvent } from "./protocol-validation";

export type SocketLike = {
  readyState: number;
  binaryType?: BinaryType;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string | ArrayBuffer>) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  send(value: string | ArrayBuffer): void;
  close(code?: number): void;
};

type LiveClientSettings = {
  url: string;
  authProtocol: `jarvis.desktop.${string}` | `jarvis.session.${string}`;
  deviceId: string;
  socketFactory?: (url: string, protocols: string[]) => SocketLike;
  onConnection: (state: OverlayView["connection"]) => void;
  onEvent: (event: ReturnType<typeof parseServerEvent>) => void;
  onAudio?: (pcm: ArrayBuffer) => void;
};

export class LiveClient {
  readonly #settings: LiveClientSettings;
  #socket: SocketLike | null = null;
  #sessionId = crypto.randomUUID();
  #turnId = crypto.randomUUID();
  #sequence = 0;
  #stopped = true;
  #retryCount = 0;
  #retryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(settings: LiveClientSettings) {
    this.#settings = settings;
  }

  start() {
    if (!this.#stopped) return;
    this.#stopped = false;
    this.#settings.onConnection("reconnecting");
    this.#open();
  }

  stop() {
    this.#stopped = true;
    if (this.#retryTimer !== null) clearTimeout(this.#retryTimer);
    this.#retryTimer = null;
    this.#socket?.close(1000);
    this.#socket = null;
  }

  activate(source: "wake_word" | "keyboard" | "ui" | "shortcut") {
    this.#turnId = crypto.randomUUID();
    this.#send("activate", { device_id: this.#settings.deviceId, source });
  }

  interrupt() {
    this.#send("interrupt", {
      device_id: this.#settings.deviceId,
      reason: "user_command",
    });
  }

  submitText(text: string) {
    const normalized = text.trim();
    if (!normalized) return;
    this.#send("submit_text", {
      device_id: this.#settings.deviceId,
      text: normalized,
    });
  }

  startTextTurn(text: string) {
    const normalized = text.trim();
    if (!normalized) return;
    this.activate("ui");
    this.submitText(normalized);
  }

  sendAudio(pcm: ArrayBuffer) {
    if (pcm.byteLength === 0 || pcm.byteLength > 8_191 || pcm.byteLength % 2 !== 0) return;
    if (this.#socket?.readyState !== 1) return;
    this.#socket.send(pcm);
  }

  decideApproval(approvalId: string, decision: "approve" | "reject") {
    this.#send("approval_decision", {
      device_id: this.#settings.deviceId,
      approval_id: approvalId,
      decision,
    });
  }

  #open() {
    if (this.#stopped) return;
    const factory = this.#settings.socketFactory ?? defaultSocketFactory;
    const socket = factory(this.#settings.url, ["jarvis.v1", this.#settings.authProtocol]);
    this.#socket = socket;
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      this.#retryCount = 0;
      this.#settings.onConnection("connected");
    };
    socket.onmessage = (message) => {
      if (message.data instanceof ArrayBuffer) {
        if (message.data.byteLength > 0 && message.data.byteLength % 2 === 0) {
          this.#settings.onAudio?.(message.data);
        }
        return;
      }
      try {
        this.#settings.onEvent(parseServerEvent(message.data));
      } catch {
        socket.close(1002);
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (this.#stopped) return;
      this.#settings.onConnection(this.#retryCount >= 2 ? "unavailable" : "reconnecting");
      const delay = Math.min(250 * 2 ** this.#retryCount, 5_000);
      this.#retryCount += 1;
      this.#retryTimer = setTimeout(() => this.#open(), delay);
    };
  }

  #send(type: ClientEvent["type"], payload: ClientEvent["payload"]) {
    if (this.#socket?.readyState !== 1) return;
    const event = {
      version: 1,
      event_id: crypto.randomUUID(),
      session_id: this.#sessionId,
      turn_id: this.#turnId,
      sequence: this.#sequence,
      timestamp: new Date().toISOString(),
      type,
      payload,
    } as ClientEvent;
    this.#sequence += 1;
    this.#socket.send(JSON.stringify(event));
  }
}

function defaultSocketFactory(url: string, protocols: string[]): SocketLike {
  return new WebSocket(url, protocols) as SocketLike;
}
