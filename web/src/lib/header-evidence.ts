export type RecordingHeaderEvidence = {
  active: boolean;
  label: string;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

export function recordingHeaderEvidence(
  response: unknown,
): RecordingHeaderEvidence {
  const unavailable = { active: false, label: "Recording status unavailable" };
  if (
    !isPlainObject(response) ||
    !["api_version", "status", "reason", "data"].every((field) =>
      Object.hasOwn(response, field),
    ) ||
    response.api_version !== "v1" ||
    response.status !== "ok" ||
    response.reason !== null
  )
    return unavailable;

  if (response.data === null)
    return { active: false, label: "Recording inactive" };
  const recording = response.data;
  if (
    !isPlainObject(recording) ||
    !Object.hasOwn(recording, "recording_id") ||
    !Object.hasOwn(recording, "status") ||
    typeof recording.recording_id !== "string" ||
    !/^[0-9a-f]{32}$/.test(recording.recording_id)
  )
    return unavailable;

  switch (recording.status) {
    case "starting":
      return { active: true, label: "Recording starting" };
    case "recording":
      return { active: true, label: "Recording active" };
    case "stopping":
      return { active: true, label: "Recording stopping" };
    case "complete":
      return { active: false, label: "Recording inactive" };
    case "failed":
      return { active: false, label: "Recording failed" };
    case "interrupted":
      return { active: false, label: "Recording interrupted" };
    default:
      return unavailable;
  }
}
