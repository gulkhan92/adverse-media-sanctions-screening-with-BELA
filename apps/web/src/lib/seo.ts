import type { Metadata } from "next";
import { brand } from "@/content/site";

/** Slug used by the social image route: path with slashes replaced, "home" for the root. */
export const ogSlug = (path: string) =>
  path === "/" ? "home" : path.replace(/^\//, "").replace(/\//g, "--");

export function pageMeta(path: string, title: string, description: string): Metadata {
  const image = `/og/${ogSlug(path)}`;
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: {
      title: `${title} | ${brand}`,
      description,
      url: path,
      images: [{ url: image, width: 1200, height: 630, alt: title }],
    },
    twitter: { card: "summary_large_image", title, description, images: [image] },
  };
}
