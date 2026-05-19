import { computeUtf16FNV1a } from "@claread/contracts";

export function hashAnchorText(text: string): string {
  return computeUtf16FNV1a(text);
}
