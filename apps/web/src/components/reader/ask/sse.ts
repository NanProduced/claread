import type { ReaderAskStreamEnvelopeDto, ReaderAskStreamEventName } from "@/types/api/reader-ask";

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
        return [
          {
            event: event as ReaderAskStreamEventName,
            data: JSON.parse(data) as Record<string, unknown>,
          },
        ];
      } catch {
        return [];
      }
    });
}

export async function consumeReaderAskSse(
  response: Response,
  onEvent: (event: ReaderAskStreamEnvelopeDto) => void,
): Promise<void> {
  if (!response.body) {
    throw new Error("Reader Ask stream body is missing.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
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
    }
  }

  if (buffer.trim()) {
    for (const event of parseSseChunk(buffer)) {
      onEvent(event);
    }
  }
}
