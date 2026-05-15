export interface TextToken {
  type: "word" | "plain";
  text: string;
  start: number;
  end: number;
}

type ScanResult = {
  end: number;
  text: string;
};

function isAsciiLetter(ch: string | undefined): boolean {
  if (!ch) {
    return false;
  }
  const code = ch.charCodeAt(0);
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122);
}

function isWordConnector(ch: string | undefined): boolean {
  return ch === "'" || ch === "’" || ch === "-";
}

function scanLetters(text: string, start: number): number {
  let i = start;
  while (i < text.length && isAsciiLetter(text[i])) {
    i += 1;
  }
  return i;
}

function scanAbbreviation(text: string, start: number): ScanResult | null {
  let i = start;
  let dottedSegments = 0;

  while (i < text.length) {
    const lettersEnd = scanLetters(text, i);
    if (lettersEnd === i || text[lettersEnd] !== ".") {
      break;
    }

    dottedSegments += 1;
    i = lettersEnd + 1;

    if (!isAsciiLetter(text[i])) {
      break;
    }
  }

  if (dottedSegments < 2) {
    return null;
  }
  return { end: i, text: text.slice(start, i) };
}

function scanPlainWord(text: string, start: number): ScanResult | null {
  let i = scanLetters(text, start);
  if (i === start) {
    return null;
  }

  while (i < text.length) {
    const connector = text[i];
    if (!isWordConnector(connector)) {
      break;
    }

    const next = text[i + 1];
    if (connector === "-") {
      if (!isAsciiLetter(next)) {
        break;
      }
      i = scanLetters(text, i + 1);
      continue;
    }

    if (!isAsciiLetter(next)) {
      i += 1;
      break;
    }
    i = scanLetters(text, i + 1);
  }

  return { end: i, text: text.slice(start, i) };
}

function scanWordLike(text: string, start: number): ScanResult | null {
  const abbreviation = scanAbbreviation(text, start);
  if (abbreviation) {
    let i = abbreviation.end;

    while (text[i] === "-") {
      const nextStart = i + 1;
      const nextToken = scanAbbreviation(text, nextStart) ?? scanPlainWord(text, nextStart);
      if (!nextToken) {
        break;
      }
      i = nextToken.end;
    }

    return { end: i, text: text.slice(start, i) };
  }

  return scanPlainWord(text, start);
}

export function tokenizeText(text: string): TextToken[] {
  const rawTokens: TextToken[] = [];
  let i = 0;

  while (i < text.length) {
    const wordLike = scanWordLike(text, i);
    if (wordLike) {
      rawTokens.push({ type: "word", text: wordLike.text, start: i, end: wordLike.end });
      i = wordLike.end;
      continue;
    }

    let plainEnd = i + 1;
    while (plainEnd < text.length && !scanWordLike(text, plainEnd)) {
      plainEnd += 1;
    }

    rawTokens.push({ type: "plain", text: text.slice(i, plainEnd), start: i, end: plainEnd });
    i = plainEnd;
  }

  const merged: TextToken[] = [];
  for (const token of rawTokens) {
    const last = merged[merged.length - 1];
    if (last && last.type === token.type) {
      last.text += token.text;
      last.end = token.end;
    } else {
      merged.push({ ...token });
    }
  }

  return merged;
}

export function findTextAnchorPosition(text: string, anchorText: string, occurrence = 1): number {
  let count = 0;
  let pos = 0;
  const safeOccurrence = occurrence || 1;

  while (count < safeOccurrence) {
    const index = text.indexOf(anchorText, pos);
    if (index === -1) {
      return -1;
    }
    count += 1;
    if (count === safeOccurrence) {
      return index;
    }
    pos = index + 1;
  }

  return -1;
}
