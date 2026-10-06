import type { Metadata } from "next";
import "./globals.css";
import ReferenceDisclosureOpener from "./ReferenceDisclosureOpener";

export const metadata: Metadata = {
  title: "GP...T · AI Race Engineer",
  description:
    "A personal AI race engineer for local F1 telemetry capture and lap analysis.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        {children}
        <ReferenceDisclosureOpener />
      </body>
    </html>
  );
}
