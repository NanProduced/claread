import { listReaderAskModelOptionsForWeb } from "@/services/bff/reader-ask";

export async function GET() {
  return listReaderAskModelOptionsForWeb();
}
