import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BrainCellGPT — One brain cell. Infinite confusion.",
  description: "A context-aware conversational AI experiment.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
