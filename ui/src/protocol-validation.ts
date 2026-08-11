import type { ErrorObject } from "ajv";

import type { ServerEvent } from "./generated";
import generatedServerEventValidator from "./generated/server-event.validator";

type ProtocolValidator = ((candidate: unknown) => candidate is ServerEvent) & {
  errors?: ErrorObject[] | null;
};

const validateServerEvent = generatedServerEventValidator as unknown as ProtocolValidator;

export class ProtocolError extends Error {
  readonly errors: ErrorObject[];

  constructor(message: string, errors: ErrorObject[] = []) {
    super(message);
    this.name = "ProtocolError";
    this.errors = errors;
  }
}

export function parseServerEvent(serialized: string): ServerEvent {
  let candidate: unknown;
  try {
    candidate = JSON.parse(serialized);
  } catch {
    throw new ProtocolError("server event is not valid JSON");
  }
  if (!validateServerEvent(candidate)) {
    throw new ProtocolError(
      "server event does not match protocol version 1",
      validateServerEvent.errors ?? [],
    );
  }
  return candidate;
}
