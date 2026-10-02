import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "APEX Engineer · Telemetry Review",
  description: "Local F1 telemetry session and lap comparison.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
