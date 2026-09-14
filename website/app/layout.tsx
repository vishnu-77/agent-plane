import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { DESCRIPTION, SITE_URL, TITLE } from "@/lib/site";
import { viewScript } from "@/lib/view";
import "./globals.css";

// Matches the console's own font stack (console/tailwind.config.ts) - one
// typeface across the whole product, not a marketing-page-only pairing.
const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans", display: "swap" });
const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: TITLE, template: "%s | agent-plane" },
  description: DESCRIPTION,
  applicationName: "agent-plane",
  keywords: [
    "AI agent authorization",
    "agent security",
    "runtime authorization",
    "AuthorityLease",
    "capability vs authority",
    "MCP gateway",
    "least privilege agents",
    "agent governance",
    "consequence governance",
    "signed audit",
  ],
  authors: [{ name: "vishnu-77", url: "https://github.com/vishnu-77" }],
  creator: "vishnu-77",
  category: "technology",
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large", "max-snippet": -1 },
  },
  openGraph: { type: "website", url: SITE_URL, siteName: "agent-plane", title: TITLE, description: DESCRIPTION },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f4ef" },
    { media: "(prefers-color-scheme: dark)", color: "#121210" },
  ],
};

// Applies the saved or system theme before first paint, so there is no flash of the wrong theme.
const themeScript = `(function(){try{var t=localStorage.getItem("agentplane-theme");if(t!=="light"&&t!=="dark"){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}if(t==="dark")document.documentElement.classList.add("dark")}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <script dangerouslySetInnerHTML={{ __html: viewScript }} />
      </head>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
