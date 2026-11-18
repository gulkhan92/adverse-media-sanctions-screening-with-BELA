import { cn } from "@/lib/cn";

export function Card({
  className,
  lift,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { lift?: boolean }) {
  return (
    <div
      className={cn(
        "rounded-card border border-line bg-white p-6 shadow-e1",
        lift && "transition duration-200 ease-out hover:-translate-y-0.5 hover:shadow-e2",
        className,
      )}
      {...props}
    />
  );
}
