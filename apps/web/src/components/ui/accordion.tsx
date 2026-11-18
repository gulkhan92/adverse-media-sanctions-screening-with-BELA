"use client";
import * as AccordionPrimitive from "@radix-ui/react-accordion";
import { ChevronDown } from "lucide-react";

export interface FaqItem {
  q: string;
  a: string;
}

export function Accordion({ items }: { items: FaqItem[] }) {
  return (
    <AccordionPrimitive.Root
      type="single"
      collapsible
      className="divide-y divide-line border-y border-line"
    >
      {items.map((item, i) => (
        <AccordionPrimitive.Item key={item.q} value={`i${i}`}>
          <AccordionPrimitive.Header>
            <AccordionPrimitive.Trigger className="group flex w-full items-center justify-between gap-4 py-5 text-start text-lg font-medium text-ink">
              {item.q}
              <ChevronDown
                className="size-5 shrink-0 text-slate-500 transition-transform duration-200 group-data-[state=open]:rotate-180"
                aria-hidden
              />
            </AccordionPrimitive.Trigger>
          </AccordionPrimitive.Header>
          <AccordionPrimitive.Content className="overflow-hidden pb-5 text-slate-700">
            {item.a}
          </AccordionPrimitive.Content>
        </AccordionPrimitive.Item>
      ))}
    </AccordionPrimitive.Root>
  );
}
