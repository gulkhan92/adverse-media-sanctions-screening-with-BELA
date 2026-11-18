"use client";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/cn";

export interface TabItem {
  id: string;
  label: string;
  content: React.ReactNode;
}

export function Tabs({ items, label }: { items: TabItem[]; label: string }) {
  const first = items[0];
  if (!first) return null;
  return (
    <TabsPrimitive.Root defaultValue={first.id}>
      <TabsPrimitive.List
        aria-label={label}
        className="mb-6 inline-flex gap-1 rounded-control border border-line bg-white p-1"
      >
        {items.map((t) => (
          <TabsPrimitive.Trigger
            key={t.id}
            value={t.id}
            className={cn(
              "rounded-md px-4 py-2 text-sm font-medium text-slate-700 transition-colors",
              "data-[state=active]:bg-ink data-[state=active]:text-white hover:text-ink",
            )}
          >
            {t.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {items.map((t) => (
        <TabsPrimitive.Content key={t.id} value={t.id}>
          {t.content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}
