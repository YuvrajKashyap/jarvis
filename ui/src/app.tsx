import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import QRCode from "qrcode";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { createDesktopPairingOffer } from "./desktop-pairing";
import type { ReadinessSnapshot } from "./generated";
import { LiveClient } from "./live-client";
import { ConversationOverlay } from "./overlay";
import { type NativePlacementContext, requestOverlayPlacement } from "./overlay-placement";
import { PhoneMicrophone } from "./phone-audio";
import { authenticatePhone, PhoneRequestError, UnpairedPhoneError } from "./phone-auth";
import { PhoneSpeaker } from "./phone-speaker";
import { sendProactivityFeedback } from "./proactivity-feedback";
import { readReadiness } from "./readiness";
import {
  desktopOverlayMovementIntent,
  initialView,
  isReminderNotification,
  reduceServerEvent,
  type SessionView,
  shouldRevealDesktopOverlay,
} from "./session";

export function App() {
  const surface = useMemo(() => (isDesktopHost() ? "desktop" : "phone"), []);
  const [view, setView] = useState<SessionView>(() => ({
    ...initialView(),
    connection: "reconnecting" as const,
  }));
  const [readiness, setReadiness] = useState<ReadinessSnapshot | null>(null);
  const client = useRef<LiveClient | null>(null);
  const connectionRef = useRef<ConnectedClient | null>(null);
  const microphone = useRef<PhoneMicrophone | null>(null);
  const speaker = useRef<PhoneSpeaker | null>(null);
  const [pairing, setPairing] = useState<{
    qrDataUrl: string | null;
    expiresAt: string | null;
    error: string | null;
  } | null>(null);

  useLayoutEffect(() => {
    document.documentElement.dataset.surface = surface;
    if (surface !== "desktop") return;
    const overlay = document.querySelector<HTMLElement>(".overlay--desktop");
    if (!overlay) return;
    const resizeOverlay = () => {
      if (overlayIsRelocating(document.documentElement)) return;
      void invoke("fit_overlay", {
        contentHeight: overlay.scrollHeight + 32,
        animate: overlayMotionEnabled(),
      });
    };
    resizeOverlay();
    const observer = new ResizeObserver(resizeOverlay);
    observer.observe(overlay);
    return () => observer.disconnect();
  }, [surface]);

  useEffect(() => {
    let disposed = false;
    let readinessTimer: number | null = null;
    void createClient(surface, {
      onConnection: (connection) => setView((current) => ({ ...current, connection })),
      onEvent: (event) => {
        setView((current) => reduceServerEvent(current, event));
        if (surface === "desktop" && isReminderNotification(event)) {
          void invoke("show_reminder_notification", { message: event.payload.message });
        }
        if (surface === "desktop" && shouldRevealDesktopOverlay(event)) {
          const window = getCurrentWindow();
          const movementIntent = desktopOverlayMovementIntent(event);
          const position = movementIntent
            ? placeDesktopOverlay(movementIntent)
            : Promise.resolve(true);
          if (shouldFocusDesktopOverlay(movementIntent)) {
            void window.show().then(() => window.setFocus());
            void position;
          } else {
            void position.then((shouldShow) => (shouldShow ? window.show() : undefined));
          }
        }
      },
      onAudio: (pcm) => void speaker.current?.play(pcm),
    })
      .then((connection) => {
        if (disposed) {
          connection.live.stop();
          return;
        }
        client.current = connection.live;
        // Keep authenticated REST metadata beside the live channel for admin actions.
        connectionRef.current = connection;
        connection.live.start();
        const refreshReadiness = () => {
          void readReadiness(connection.baseUrl, connection.token)
            .then((snapshot) => {
              if (!disposed) setReadiness(snapshot);
            })
            .catch(() => {
              if (!disposed) setReadiness(unavailableReadiness());
            });
        };
        refreshReadiness();
        readinessTimer = window.setInterval(refreshReadiness, 60_000);
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setView((current) => ({
            ...current,
            connection: "unavailable",
            state: "unavailable",
            detail: surface === "phone" ? phoneConnectionFailureMessage(error) : current.detail,
          }));
        }
      });
    return () => {
      disposed = true;
      if (readinessTimer !== null) window.clearInterval(readinessTimer);
      client.current?.stop();
      client.current = null;
      connectionRef.current = null;
      void microphone.current?.stop();
      microphone.current = null;
      void speaker.current?.close();
      speaker.current = null;
    };
  }, [surface]);

  useEffect(() => {
    if (surface !== "desktop" || view.state !== "idle" || pairing) return;
    const hideDelay = desktopIdleHideDelay(
      view.detail,
      view.transcript.length > 0 || view.suggestion !== null,
    );
    const timer = window.setTimeout(() => void getCurrentWindow().hide(), hideDelay);
    return () => window.clearTimeout(timer);
  }, [pairing, surface, view.detail, view.state, view.suggestion, view.transcript.length]);

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

  useEffect(() => {
    if (surface !== "desktop") return;
    let disposed = false;
    const removers: Array<() => void> = [];
    void Promise.all([
      listen<string>("jarvis://overlay-transit", (event) => {
        setOverlayTransitState(document.documentElement, event.payload);
      }),
      listen("jarvis://overlay-settled", () => {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            setOverlayTransitState(document.documentElement, null);
          });
        });
      }),
    ]).then((unlisteners) => {
      if (disposed) {
        for (const unlisten of unlisteners) unlisten();
      } else {
        removers.push(...unlisteners);
      }
    });
    return () => {
      disposed = true;
      for (const remove of removers) remove();
      setOverlayTransitState(document.documentElement, null);
    };
  }, [surface]);

  return (
    <ConversationOverlay
      surface={surface}
      view={view}
      readiness={readiness}
      pairing={pairing}
      onApprove={(approvalId) => client.current?.decideApproval(approvalId, "approve")}
      onReject={(approvalId) => client.current?.decideApproval(approvalId, "reject")}
      onInterrupt={() => {
        void speaker.current?.cancel();
        client.current?.interrupt();
      }}
      onSubmit={(text) => {
        setView((current) => ({ ...current, suggestion: null }));
        submitFromComposer(client.current, view.state, text);
      }}
      onDismissSuggestion={() => setView((current) => ({ ...current, suggestion: null }))}
      onSuggestionFeedback={(feedback) => {
        const suggestionId = view.suggestion?.id;
        const activeConnection = connectionRef.current;
        setView((current) => ({ ...current, suggestion: null }));
        if (!suggestionId || !activeConnection) return;
        void sendProactivityFeedback(
          activeConnection.baseUrl,
          activeConnection.token,
          suggestionId,
          feedback,
        ).catch(() => {
          setView((current) => ({
            ...current,
            detail: "I couldn't save that suggestion preference. Try again when connected.",
          }));
        });
      }}
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
      onMoveOverlay={() => void invoke("begin_overlay_drag")}
      onResetOverlay={() =>
        void invoke("reset_overlay_position", { animate: overlayMotionEnabled() })
      }
      onRetryConnection={() => window.location.reload()}
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

export function desktopIdleHideDelay(detail: string | null, hasConversation: boolean): number {
  if (hasConversation) return 120_000;
  return detail ? 30_000 : 20_000;
}

export function overlayMotionEnabled(
  matchMedia: ((query: string) => { matches: boolean }) | undefined = window.matchMedia?.bind(
    window,
  ),
): boolean {
  return !matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export function shouldFocusDesktopOverlay(
  intent: ReturnType<typeof desktopOverlayMovementIntent>,
): boolean {
  return intent === "conversation";
}

export function setOverlayTransitState(
  root: Pick<HTMLElement, "dataset">,
  state: string | null,
): void {
  if (state) {
    root.dataset.overlayTransit = state;
  } else {
    delete root.dataset.overlayTransit;
  }
}

export function overlayIsRelocating(root: Pick<HTMLElement, "dataset">): boolean {
  return Boolean(root.dataset.overlayTransit);
}

export function submitFromComposer(
  client: Pick<LiveClient, "startTextTurn" | "submitText"> | null,
  state: SessionView["state"],
  text: string,
): void {
  if (state === "idle") {
    client?.startTextTurn(text);
    return;
  }
  if (state === "listening") client?.submitText(text);
}

export function phoneConnectionFailureMessage(error: unknown): string {
  if (error instanceof UnpairedPhoneError) {
    return "This iPhone is not paired. Open JARVIS on your laptop and scan a new one-use QR code.";
  }
  if (error instanceof PhoneRequestError && error.status === 400) {
    return "This pairing code expired or was already used. Generate a new code from your laptop.";
  }
  if (error instanceof PhoneRequestError && error.status === 401) {
    return "This phone identity is no longer authorized. Pair it again from your laptop.";
  }
  return "The private JARVIS connection could not be completed. Confirm Tailscale is connected, then try again.";
}

type ClientCallbacks = Pick<
  ConstructorParameters<typeof LiveClient>[0],
  "onAudio" | "onConnection" | "onEvent"
>;

type ConnectedClient = {
  live: LiveClient;
  baseUrl: string;
  token: string;
};

async function createClient(
  surface: "desktop" | "phone",
  callbacks: ClientCallbacks,
): Promise<ConnectedClient> {
  if (surface === "desktop") {
    const token = await invoke<string>("desktop_session_token");
    const baseUrl = "http://127.0.0.1:7331";
    return {
      baseUrl,
      token,
      live: new LiveClient({
        ...callbacks,
        url: "ws://127.0.0.1:7331/v1/live",
        authProtocol: `jarvis.desktop.${token}`,
        deviceId: "desktop",
      }),
    };
  }

  const session = await authenticatePhone(window.location.origin);
  const webSocketScheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return {
    baseUrl: window.location.origin,
    token: session.token,
    live: new LiveClient({
      ...callbacks,
      url: `${webSocketScheme}//${window.location.host}/v1/live`,
      authProtocol: `jarvis.session.${session.token}`,
      deviceId: session.deviceId,
    }),
  };
}

function unavailableReadiness(): ReadinessSnapshot {
  return {
    overall: "degraded",
    generated_at: new Date().toISOString(),
    checks: [
      {
        code: "diagnostics_endpoint",
        state: "degraded",
        summary: "JARVIS could not verify subsystem readiness.",
        detail: "The live conversation may still work, but readiness is currently unknown.",
      },
    ],
  };
}

function isDesktopHost(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

async function placeDesktopOverlay(intent: "conversation" | "proactive"): Promise<boolean> {
  try {
    const [token, context] = await Promise.all([
      invoke<string>("desktop_session_token"),
      invoke<NativePlacementContext>("overlay_placement_context"),
    ]);
    const plan = await requestOverlayPlacement(token, context, intent);
    if (plan === null) return true;
    return await invoke<boolean>("apply_content_aware_placement", {
      intent,
      disposition: plan.disposition,
      targetLeft: plan.target.left,
      targetTop: plan.target.top,
      targetWidth: plan.target.width,
      targetHeight: plan.target.height,
      animate: overlayMotionEnabled(),
    });
  } catch {
    await invoke("move_overlay_for_attention", {
      intent,
      animate: overlayMotionEnabled(),
    }).catch(() => undefined);
    return true;
  }
}
