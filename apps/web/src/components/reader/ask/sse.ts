import type {
  ReaderAskStreamEnvelopeDto,
  ReaderAskStreamEventName,
} from "@/types/api/reader-ask";
import {
  isReaderAskAgenticCompletedPayload,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
} from "@/types/api/reader-ask";

export {
  isReaderAskAgenticCompletedPayload,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
};

function parseSseChunk(chunk: string): ReaderAskStreamEnvelopeDto[] {
  return chunk
    .split("\n\n")
    .map((part) => part.trim())
    .filter(Boolean)
    .flatMap((part) => {
      const lines = part.split("\n");
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const data = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");

      if (!event || !data) {
        return [];
      }

      try {
        // Preserve typed agentic payloads as-is (no remapping to legacy shapes
        // such as content_md / article_rag). Unknown event names still pass
        // through for forward-compat; consumers must not treat them as success.
        return [
          {
            event: event as ReaderAskStreamEventName,
            data: JSON.parse(data) as Record<string, unknown>,
          },
        ];
      } catch (parseError) {
        return [
          {
            event: "error" as ReaderAskStreamEventName,
            data: {
              code: "SSE_PARSE_ERROR",
              detail: `Failed to parse SSE data for event "${event}": ${parseError instanceof Error ? parseError.message : String(parseError)}`,
              raw_data: data,
            },
          },
        ];
      }
    });
}

export async function consumeReaderAskSse(
  response: Response,
  onEvent: (event: ReaderAskStreamEnvelopeDto) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) {
    throw new Error("Reader Ask stream body is missing.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let parseErrorEncountered = false;

  try {
    while (true) {
      if (signal?.aborted || parseErrorEncountered) {
        break;
      }
      const { value, done } = await reader.read();
      if (done || signal?.aborted || parseErrorEncountered) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      const boundary = buffer.lastIndexOf("\n\n");
      if (boundary === -1) {
        continue;
      }

      const ready = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const event of parseSseChunk(ready)) {
        onEvent(event);
        if (
          event.event === "error" &&
          (event.data as Record<string, unknown>)?.code === "SSE_PARSE_ERROR"
        ) {
          parseErrorEncountered = true;
          break;
        }
      }
    }

    // Do not process trailing buffer after a parse error — the stream is
    // considered corrupted and subsequent events cannot be trusted.
    if (!parseErrorEncountered && buffer.trim()) {
      for (const event of parseSseChunk(buffer)) {
        onEvent(event);
      }
    }
  } finally {
    reader.releaseLock?.();
  }
}
