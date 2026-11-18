import { MDXRemote } from "next-mdx-remote/rsc";

export function Mdx({ source }: { source: string }) {
  return (
    <div className="prose-doc">
      <MDXRemote source={source} />
    </div>
  );
}
