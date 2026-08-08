import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import QRCode from "qrcode";
import { useEffect, useMemo, useRef, useState } from "react";

import { createDesktopPairingOffer } from "./desktop-pairing";
import { LiveClient } from "./live-client";
import { ConversationOverlay } from "./overlay";
import { PhoneMicrophone } from "./phone-audio";
import { authenticatePhone } from "./phone-auth";
import { PhoneSpeaker } from "./phone-speaker";
import { initialView, reduceServerEvent, type SessionView } from "./session";

export function App() {
  const surface = useMemo(() => (isDesktopHost() ? "desktop" : "phone"), []);
  const [view, setView] = useState<SessionView>(() => ({
    ...initialView(),
    connection: surface === "desktop" ? ("reconnecting" as const) : ("unavailable" as const),
  }));
  const client = useRef<LiveClient | null>(null);
  const microphone = useRef<PhoneMicrophone | null>(null);
  const speaker = useRef<PhoneSpeaker | null>(null);
  const [pairing, setPairing] = useState<{
    qrDataUrl: string | null;
    expiresAt: string | null;
    error: string | null;
  } | null>(null);

  useEffect(() => {
    let disposed = false;
    void createClient(surface, {
      onConnection: (connection) => setView((current) => ({ ...current, connection })),
      onEvent: (event) => {
        setView((current) => reduceServerEvent(current, event));
        if (surface === "desktop" && event.type === "state_changed") {
          if (event.payload.state === "listening") {
            void getCurrentWindow()
              .show()
              .then(() => getCurrentWindow().setFocus());
          }
        }
      },
      onAudio: (pcm) => void speaker.current?.play(pcm),
    })
      .then((connection) => {
        if (disposed) {
          connection.stop();
          return;
        }
        client.current = connection;
        connection.start();
      })
      .catch(() => {
        if (!disposed) setView((current) => ({ ...current, connection: "unavailable" }));
      });
    return () => {
      disposed = true;
      client.current?.stop();
      client.current = null;
      void microphone.current?.stop();
      microphone.current = null;
      void speaker.current?.close();
      speaker.current = null;
    };
  }, [surface]);

  useEffect(() => {
    if (surface !== "desktop" || view.state !== "idle" || pairing) return;
    const timer = window.setTimeout(() => void getCurrentWindow().hide(), 5_000);
    return () => window.clearTimeout(timer);
  }, [pairing, surface, view.state]);

  useEffect(() => {
    if (surface === "phone" && view.state === "idle") void microphone.current?.stop();
    if (surface === "phone" && view.state === "listening") void speaker.current?.cancel();
  }, [surface, view.state]);

  useEffect(() => {
    if (surface !== "desktop") return;
    let disposed = false;
    let removeListener: (() => void) | null = null;
    void listen<string>("jarvis://activate", (event) => {
      const source = event.payload === "shortcut" ? "shortcut" : "ui";
      client.current?.activate(source);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else removeListener = unlisten;
    });
    return () => {
      disposed = true;
      removeListener?.();
    };
  }, [surface]);

  return (
    <ConversationOverlay
      surface={surface}
      view={view}
      pairing={pairing}
      onApprove={(approvalId) => client.current?.decideApproval(approvalId, "approve")}
      onReject={(approvalId) => client.current?.decideApproval(approvalId, "reject")}
      onInterrupt={() => {
        void speaker.current?.cancel();
        client.current?.interrupt();
      }}
      onSubmit={(text) => client.current?.submitText(text)}
      onActivate={() => {
        if (surface !== "phone" || !client.current) return;
        const audio = microphone.current ?? new PhoneMicrophone();
        const output = speaker.current ?? new PhoneSpeaker();
        microphone.current = audio;
        speaker.current = output;
        void Promise.all([output.start(), audio.start((pcm) => client.current?.sendAudio(pcm))])
          .then(() => {
            client.current?.activate("ui");
          })
          .catch(() => {
            setView((current) => ({
              ...current,
              detail: "Microphone access is required to speak with JARVIS.",
            }));
          });
      }}
      onPairPhone={() => {
        setPairing({ qrDataUrl: null, expiresAt: null, error: null });
        void invoke<string>("desktop_session_token")
          .then((token) => createDesktopPairingOffer(token))
          .then(async (offer) => ({
            qrDataUrl: await QRCode.toDataURL(offer.pairingUrl, {
              errorCorrectionLevel: "M",
              margin: 2,
              width: 232,
              color: { dark: "#0b1118", light: "#ffffff" },
            }),
            expiresAt: offer.expiresAt,
          }))
          .then((offer) => setPairing({ ...offer, error: null }))
          .catch((error: unknown) => {
            setPairing({
              qrDataUrl: null,
              expiresAt: null,
              error: error instanceof Error ? error.message : "Phone pairing is unavailable.",
            });
          });
      }}
      onClosePairing={() => setPairing(null)}
    />
  );
}

type ClientCallbacks = Pick<
  ConstructorParameters<typeof LiveClient>[0],
  "onAudio" | "onConnection" | "onEvent"
>;

async function createClient(
  surface: "desktop" | "phone",
  callbacks: ClientCallbacks,
): Promise<LiveClient> {
  if (surface === "desktop") {
    const token = await invoke<string>("desktop_session_token");
    return new LiveClient({
      ...callbacks,
      url: "ws://127.0.0.1:7331/v1/live",
      authProtocol: `jarvis.desktop.${token}`,
      deviceId: "desktop",
    });
  }

  const session = await authenticatePhone(window.location.origin);
  const webSocketScheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return new LiveClient({
    ...callbacks,
    url: `${webSocketScheme}//${window.location.host}/v1/live`,
    authProtocol: `jarvis.session.${session.token}`,
    deviceId: session.deviceId,
  });
}

function isDesktopHost(): boolean {
  return "__TAURI_INTERNALS__" in window;
}
