import type { LucideIcon } from "lucide-react";
import {
  Banknote,
  Bitcoin,
  Building2,
  Briefcase,
  Network,
  Newspaper,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Landmark,
  Layers,
  Search,
  Share2,
  Workflow,
  FileSearch,
  Gavel,
  Cpu,
  BarChart3,
  Plug,
  Languages,
} from "lucide-react";

export const brand = "Atlas Screening";
export const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
export const contactEmail = "contact@atlas-screening.example";

export interface MenuItem {
  href: string;
  title: string;
  description: string;
  icon: LucideIcon;
}

export const platformMenu: MenuItem[] = [
  {
    href: "/platform#ingestion",
    title: "Ingestion",
    description: "Collect and normalize multilingual news.",
    icon: Newspaper,
  },
  {
    href: "/platform#entity-linking",
    title: "Entity linking",
    description: "Connect names to verified identities.",
    icon: Languages,
  },
  {
    href: "/platform#matching",
    title: "Matching and scoring",
    description: "Match against lists with explained scores.",
    icon: Search,
  },
  {
    href: "/platform#review",
    title: "Review and cases",
    description: "Triage alerts and keep an audit trail.",
    icon: Gavel,
  },
  {
    href: "/platform#analytics",
    title: "Analytics",
    description: "Measure workload, precision, and latency.",
    icon: BarChart3,
  },
  {
    href: "/developers",
    title: "API",
    description: "Single and batch screening over HTTPS.",
    icon: Plug,
  },
];

export const solutionsMenu: MenuItem[] = [
  {
    href: "/solutions/adverse-media",
    title: "Adverse media screening",
    description: "Find negative news about the right person.",
    icon: Newspaper,
  },
  {
    href: "/solutions/sanctions-and-pep",
    title: "Sanctions and PEP screening",
    description: "Screen against official lists with fewer false hits.",
    icon: ShieldAlert,
  },
  {
    href: "/solutions/network-analysis",
    title: "Network analysis",
    description: "Follow ownership and associations.",
    icon: Share2,
  },
];

export interface Industry {
  slug: string;
  title: string;
  short: string;
  icon: LucideIcon;
  image: string;
  alt: string;
}

export const industries: Industry[] = [
  {
    slug: "banking",
    title: "Banking",
    short: "Customer and counterparty screening at scale.",
    icon: Landmark,
    image: "banking",
    alt: "Abstract architectural lines in blue",
  },
  {
    slug: "payments-and-fintech",
    title: "Payments and fintech",
    short: "Fast checks at onboarding and on every payee.",
    icon: Banknote,
    image: "payments",
    alt: "Abstract network of connected payment nodes",
  },
  {
    slug: "digital-assets",
    title: "Digital assets",
    short: "Screening for exchanges and custodians.",
    icon: Bitcoin,
    image: "digital-assets",
    alt: "Abstract chart lines on a dark background",
  },
  {
    slug: "insurance",
    title: "Insurance",
    short: "Policyholder and beneficiary due diligence.",
    icon: ShieldCheck,
    image: "insurance",
    alt: "Abstract layered documents",
  },
  {
    slug: "legal-and-professional-services",
    title: "Legal and professional services",
    short: "Client and matter due diligence.",
    icon: Scale,
    image: "legal",
    alt: "Abstract vertical shelving lines",
  },
  {
    slug: "corporate-compliance",
    title: "Corporate compliance",
    short: "Third party and supplier due diligence.",
    icon: Building2,
    image: "corporate",
    alt: "Abstract modern office geometry",
  },
];

export const industriesMenu: MenuItem[] = industries.map((i) => ({
  href: `/industries/${i.slug}`,
  title: i.title,
  description: i.short,
  icon: i.icon,
}));

export const featured = {
  platform: {
    title: "How the pipeline works",
    body: "From ingestion to a reviewed alert, in one diagram.",
    href: "/platform",
    cta: "See the platform",
  },
  solutions: {
    title: "Measured, not claimed",
    body: "Read the evaluation design and current results.",
    href: "/methodology",
    cta: "Read the methodology",
  },
  industries: {
    title: "Illustrative scenarios",
    body: "Typical risk themes and workflows by sector.",
    href: "/industries/banking",
    cta: "Explore a sector",
  },
};

export const resourcesMenu = [
  { href: "/resources", title: "Articles" },
  { href: "/methodology", title: "Methodology" },
  { href: "/developers", title: "Developers" },
  { href: "/security", title: "Security" },
];

export const companyMenu = [
  { href: "/company", title: "About" },
  { href: "/contact", title: "Contact" },
  { href: "/pricing", title: "Pricing" },
];

export const footerColumns = [
  {
    key: "platform",
    title: "Platform",
    links: platformMenu.map((m) => ({ href: m.href, label: m.title })),
  },
  {
    key: "solutions",
    title: "Solutions",
    links: solutionsMenu.map((m) => ({ href: m.href, label: m.title })),
  },
  {
    key: "industries",
    title: "Industries",
    links: industries.map((i) => ({ href: `/industries/${i.slug}`, label: i.title })),
  },
  {
    key: "resources",
    title: "Resources",
    links: resourcesMenu.map((m) => ({ href: m.href, label: m.title })),
  },
  {
    key: "company",
    title: "Company",
    links: [
      ...companyMenu.map((m) => ({ href: m.href, label: m.title })),
      { href: "/legal/privacy", label: "Privacy" },
    ],
  },
];

export const capabilityIcons = { Layers, Network, Workflow, FileSearch, ShieldAlert, Cpu };
