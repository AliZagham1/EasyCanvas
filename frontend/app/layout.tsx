import type { Metadata } from "next";
import { Suspense } from "react";
import localFont from "next/font/local";
import { Geist_Mono } from "next/font/google";
import Sidebar from "./components/sidebar";
import { AIConsentProvider } from "./components/ai-consent-provider";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import "./globals.css";

const inter = localFont({
  src: "./fonts/InterVariable.woff2",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "EasyCanvas",
  description: "Academic planning dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="h-full">
        <AIConsentProvider>
        <div className="app-shell">
          {/* The sidebar reads the URL; routes with a dynamic segment (/courses/[courseId]) need it inside Suspense. */}
          <Suspense fallback={<aside className="sidebar" aria-hidden="true" />}>
            <Sidebar />
          </Suspense>
          <main className="app-main">{children}</main>
        </div>
        </AIConsentProvider>
      </body>
    </html>
  );
}
