import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GP...T · Race Engineer",
  description: "Local-first F1 telemetry capture and lap analysis.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
