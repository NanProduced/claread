import { ImageResponse } from "next/og";

export const alt = "Claread shared result";
export const size = {
  width: 1200,
  height: 630,
};
export const contentType = "image/png";

type OgImageProps = {
  params: Promise<{ shareId: string }>;
};

export default async function Image({ params }: OgImageProps) {
  const { shareId } = await params;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          background: "#fbfaf7",
          color: "#191816",
          padding: 72,
        }}
      >
        <div style={{ fontSize: 36, color: "#275d4a" }}>Claread</div>
        <div style={{ marginTop: 32, fontSize: 72, fontWeight: 700 }}>
          Shared reading result
        </div>
        <div style={{ marginTop: 24, fontSize: 30, color: "#6f6a62" }}>
          {shareId}
        </div>
      </div>
    ),
    size,
  );
}
