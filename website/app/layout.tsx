import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Space_Grotesk } from "next/font/google";
import { DESCRIPTION, SITE_URL, TITLE } from "@/lib/site";
import { viewScript } from "@/lib/view";
import "./globals.css";

const grotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-grotesk", display: "swap" });
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
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
    { media: "(prefers-color-scheme: light)", color: "#f7f8f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1211" },
  ],
};

// Applies the saved or system theme before first paint, so there is no flash of the wrong theme.
const themeScript = `(function(){try{var t=localStorage.getItem("agentplane-theme");if(t!=="light"&&t!=="dark"){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}if(t==="dark")document.documentElement.classList.add("dark")}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${grotesk.variable} ${jetbrains.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <script dangerouslySetInnerHTML={{ __html: viewScript }} />
      </head>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
