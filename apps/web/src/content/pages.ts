import type { FaqItem } from "@/components/ui/accordion";

export interface Solution {
  slug: string;
  title: string;
  lead: string;
  problem: string[];
  approach: string[];
  workflow: string[];
  outcomes: { label: string; value: string; pending?: boolean }[];
  faq: FaqItem[];
  art: "network" | "lines" | "grid";
}

export const solutions: Solution[] = [
  {
    slug: "adverse-media",
    title: "Adverse media screening",
    lead: "Find negative news about the person or company you are screening, and set aside the stories about someone with the same name.",
    problem: [
      "Adverse media screening searches news for allegations of financial crime, corruption, fraud, and similar conduct. Keyword search returns every article that contains the name, so common names produce long lists of unrelated stories.",
      "News is also multilingual. A story about a subject in Brazil, Egypt, or Indonesia is written in Portuguese, Arabic, or Indonesian, and the name may be transliterated in several ways.",
    ],
    approach: [
      "Each article is processed once at ingestion. Language is detected, the text is split into passages, and names are linked to entities in a shared knowledge base across 97 languages. Names that have no knowledge base entry are kept as unlinked mentions and matched to subjects using name similarity and context such as country and role.",
      "A local language model classifies the finding by category and severity, and the supporting passage is stored for the reviewer. Screening a subject then reads stored results, so a single check is fast.",
    ],
    workflow: [
      "Ingest and clean articles",
      "Link names to entities",
      "Classify category and severity",
      "Match to subjects and score",
      "Review the highlighted evidence",
    ],
    outcomes: [
      { label: "Median screening latency (stored results)", value: "5 ms" },
      {
        label: "Precision and recall on a hand-labeled adverse media set",
        value: "Pending",
        pending: true,
      },
      {
        label: "Reduction in false matches versus fuzzy matching",
        value: "Pending",
        pending: true,
      },
    ],
    faq: [
      {
        q: "Which languages are covered?",
        a: "The linking model covers 97 languages. Coverage varies by language, and the methodology page reports results per language once the evaluation has run.",
      },
      {
        q: "Does the system decide whether a finding is true?",
        a: "No. Findings are leads for human review. The reviewer sees the passage, the linked entity, and the score breakdown, and records the decision.",
      },
      {
        q: "How is untrusted article text handled?",
        a: "Article text is treated as untrusted input to the language model. It is delimited, instructions inside it are ignored, and model output is validated against a schema.",
      },
    ],
    art: "lines",
  },
  {
    slug: "sanctions-and-pep",
    title: "Sanctions and PEP screening",
    lead: "Screen names against official sanctions lists and politically exposed person data, with scores that explain why a result was returned.",
    problem: [
      "Sanctions lists record names in many scripts and spellings, with aliases and partial identifiers. Matching on exact strings misses real hits, and matching loosely returns many that are not.",
      "Teams also need to rescreen customers whenever a list changes, without repeating work on cases that were already cleared.",
    ],
    approach: [
      "Subjects are compared with list entries and aliases using trigram similarity and name embeddings. Date of birth, country, and identifiers raise or lower confidence. The score is a documented weighted formula and every component is shown to the reviewer.",
      "Monitoring reruns screening when lists update and creates alerts only for new or changed matches. Decisions on earlier alerts are kept as feedback.",
    ],
    workflow: [
      "Add subjects or upload a file",
      "Compare with lists and aliases",
      "Score with an explained breakdown",
      "Review and resolve",
      "Monitor for new matches",
    ],
    outcomes: [
      { label: "Watchlist entries indexed in the demonstration environment", value: "19,563" },
      { label: "Median screening latency", value: "5 ms" },
      {
        label: "Reduction in false matches versus fuzzy matching",
        value: "Pending",
        pending: true,
      },
    ],
    faq: [
      {
        q: "Which lists are used?",
        a: "The primary source is the U.S. Treasury OFAC consolidated lists, which are public. Other sources are listed with their licenses on the methodology page.",
      },
      {
        q: "Can I change how scores are weighted?",
        a: "Yes, within bounds. Tenant administrators can adjust thresholds and weights, and each change is recorded in the audit log.",
      },
      {
        q: "Is a match a legal determination?",
        a: "No. A match is a lead for human review. Legal determinations remain with your compliance function.",
      },
    ],
    art: "grid",
  },
  {
    slug: "network-analysis",
    title: "Network analysis",
    lead: "Follow ownership and associations from a subject to the people and companies around it, using public registry and leak data.",
    problem: [
      "A subject that is clean on its own may be owned or controlled by a listed party. Ownership data is spread across registries, and the links are rarely visible in a name search.",
    ],
    approach: [
      "The platform builds a graph from the Global LEI System relationship records, the ICIJ Offshore Leaks database, and co-occurrence in news. Analysts can expand neighbors, filter by relationship type, and open the source of each edge.",
      "The graph shows what the sources contain and nothing more. Absence of a link in the data does not show absence of a relationship.",
    ],
    workflow: [
      "Open a subject or entity",
      "Expand owners and associates",
      "Filter by relationship and source",
      "Inspect each edge's source",
      "Attach findings to a case",
    ],
    outcomes: [
      { label: "Ownership sources", value: "GLEIF, ICIJ Offshore Leaks" },
      { label: "Watchlist entries indexed in the demonstration environment", value: "19,563" },
    ],
    faq: [
      {
        q: "How complete is the ownership data?",
        a: "It is limited to what the public sources contain. The article on ownership networks explains what registry and leak data can and cannot show.",
      },
    ],
    art: "network",
  },
];

export interface IndustryDetail {
  slug: string;
  themes: string[];
  scenario: { title: string; text: string };
  workflow: string[];
  related: string[];
}

export const industryDetails: IndustryDetail[] = [
  {
    slug: "banking",
    themes: [
      "Politically exposed persons and their associates",
      "Correspondent and trade finance counterparties",
      "Corruption and fraud allegations in local-language press",
    ],
    scenario: {
      title: "A commercial client with a common name",
      text: "A bank reviews a trading company whose name is shared by several businesses in different countries. Keyword search returns dozens of stories. Entity linking separates the stories that refer to the client from those about namesakes, and the analyst reviews two passages instead of forty.",
    },
    workflow: [
      "Screen at onboarding and rescreen on list updates",
      "Review linked evidence, not raw hits",
      "Escalate to a case with notes",
      "Export a report for the file",
    ],
    related: ["Sanctions and PEP screening", "Batch screening", "Audit trail"],
  },
  {
    slug: "payments-and-fintech",
    themes: [
      "High-volume onboarding with short decision windows",
      "Payee and merchant screening",
      "Cross-border names in several scripts",
    ],
    scenario: {
      title: "A merchant onboarded in minutes",
      text: "A payments provider screens each new merchant and its principals through the API during onboarding. Because results are read from stored data, the check returns in milliseconds and only meaningful matches reach the review queue.",
    },
    workflow: [
      "Call the screening API at signup",
      "Route clear results automatically",
      "Queue possible matches for review",
      "Monitor the portfolio daily",
    ],
    related: ["API and batch screening", "Continuous monitoring", "Rate limits and idempotency"],
  },
  {
    slug: "digital-assets",
    themes: [
      "Counterparty and customer due diligence",
      "Entities named in enforcement news across jurisdictions",
      "Ownership of corporate customers",
    ],
    scenario: {
      title: "A corporate customer with offshore owners",
      text: "An exchange onboards a corporate customer. Network analysis shows an owner that appears in a leak database. The analyst reads the source record, notes that it does not establish wrongdoing, and attaches the finding to the case.",
    },
    workflow: [
      "Screen the entity and its principals",
      "Expand ownership in the network view",
      "Record the reasoning in a case",
      "Rescreen when sources change",
    ],
    related: ["Network analysis", "Case management", "Audit trail"],
  },
  {
    slug: "insurance",
    themes: [
      "Beneficiaries and policyholders",
      "Claims fraud reported in regional news",
      "Intermediaries and brokers",
    ],
    scenario: {
      title: "A beneficiary in another language",
      text: "An insurer reviews a large claim paid to a beneficiary abroad. Articles in the local language describe a fraud inquiry. Multilingual linking connects them to the beneficiary where English search found nothing.",
    },
    workflow: [
      "Screen parties at policy issue and at claim",
      "Review multilingual findings",
      "Document the decision",
      "Report to the claims committee",
    ],
    related: ["Adverse media screening", "Reports", "Multilingual entity linking"],
  },
  {
    slug: "legal-and-professional-services",
    themes: [
      "Client acceptance and matter-level due diligence",
      "Beneficial ownership of corporate clients",
      "Reputational risk of counterparties",
    ],
    scenario: {
      title: "A new corporate client",
      text: "A law firm checks a prospective client and its directors before accepting a matter. The screening report lists each result with its source, and the file records who reviewed it and when.",
    },
    workflow: [
      "Screen the client and related parties",
      "Resolve each alert with a reason",
      "Produce a report for the file",
      "Rescreen on a schedule",
    ],
    related: ["Reports", "Case management", "Network analysis"],
  },
  {
    slug: "corporate-compliance",
    themes: [
      "Third-party and supplier due diligence",
      "Sanctions exposure in the supply chain",
      "Adverse media on agents and distributors",
    ],
    scenario: {
      title: "A supplier base of several thousand",
      text: "A compliance team uploads its supplier list as a CSV. Batch screening returns results with progress and a downloadable file, and continuous monitoring raises an alert only when something changes.",
    },
    workflow: [
      "Upload suppliers in batch",
      "Triage by risk level",
      "Assign alerts to analysts",
      "Track queue age and throughput",
    ],
    related: ["Batch screening", "Continuous monitoring", "Operational analytics"],
  },
];

export const plans = [
  {
    code: "starter",
    name: "Starter",
    price: "499",
    included: "5,000",
    overage: "40",
    blurb: "For small teams starting to screen customers and counterparties.",
  },
  {
    code: "professional",
    name: "Professional",
    price: "1,999",
    included: "50,000",
    overage: "30",
    blurb: "For compliance teams with daily screening volume and case work.",
  },
  {
    code: "enterprise",
    name: "Enterprise",
    price: "From 8,000",
    included: "500,000",
    overage: "20",
    blurb: "For organizations with high volume and tailored arrangements.",
  },
];

export const included = [
  "Single and batch screening",
  "Continuous monitoring",
  "Alert queue, case management, and audit trail",
  "Entity network graph",
  "Operational analytics",
  "Screening reports as PDF",
  "API access with keys",
];

export const comparison: { feature: string; values: [string, string, string] }[] = [
  { feature: "Monthly price (USD)", values: ["499", "1,999", "From 8,000"] },
  { feature: "Included screenings per month", values: ["5,000", "50,000", "500,000"] },
  { feature: "Overage per 1,000 screenings (USD)", values: ["40", "30", "20"] },
  { feature: "Annual billing discount", values: ["10%", "10%", "10%"] },
  { feature: "Capabilities listed above", values: ["All", "All", "All"] },
];

export const pricingFaq: FaqItem[] = [
  {
    q: "What counts as a screening?",
    a: "One subject checked against the watchlists. Each single or batch screening request counts once per subject.",
  },
  {
    q: "What happens above the included volume?",
    a: "Usage above the included amount is billed per 1,000 additional screenings at the overage rate of the plan.",
  },
  {
    q: "Do the plans differ in capabilities?",
    a: "Not in this demonstration. The plans differ in included volume, overage rate, and price.",
  },
  {
    q: "Are these real prices?",
    a: "No. These are illustrative prices for the demonstration environment. No payments are processed.",
  },
];
