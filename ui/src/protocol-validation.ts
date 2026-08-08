import Ajv2020, { type ErrorObject } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import type { ServerEvent } from "./generated";
import serverEventSchema from "./generated/server-event.schema.json";

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
const validationSchema: Record<string, unknown> = { ...serverEventSchema };
delete validationSchema.discriminator;
const validateServerEvent = ajv.compile<ServerEvent>(validationSchema);

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
