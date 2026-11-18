"use client";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useRouter } from "@/i18n/navigation";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/form";
import { messageOf, post } from "@/lib/api";
import { industries } from "@/content/site";

const schema = z.object({
  name: z.string().trim().min(2, "Enter your name.").max(200),
  email: z.email("Enter a valid work email."),
  company: z.string().trim().min(2, "Enter your organization.").max(200),
  role: z.string().trim().min(2, "Enter your role.").max(200),
  industry: z.string().min(1, "Select an industry."),
  message: z.string().trim().min(10, "Write at least 10 characters.").max(2000),
  consent: z.boolean().refine((v) => v, "Consent is required so that we can contact you."),
  website: z.string().max(200).optional(),
});
type Values = z.infer<typeof schema>;

export function ContactForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setValue,
    control,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", consent: false, industry: "" },
  });

  const prefill = useSearchParams().get("email");
  useEffect(() => {
    if (prefill) setValue("email", prefill);
  }, [prefill, setValue]);

  async function onSubmit(v: Values) {
    setError(null);
    try {
      await post("/contact/demo-request", {
        name: v.name,
        email: v.email,
        company: v.company,
        role: v.role,
        industry: v.industry,
        message: v.message,
        consent: v.consent,
        website: v.website || null,
      });
      router.push("/contact/thanks");
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Name" htmlFor="name" error={errors.name?.message}>
          <Input
            id="name"
            autoComplete="name"
            aria-invalid={!!errors.name}
            aria-describedby={errors.name ? "name-error" : undefined}
            {...register("name")}
          />
        </Field>
        <Field label="Work email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            aria-invalid={!!errors.email}
            aria-describedby={errors.email ? "email-error" : undefined}
            {...register("email")}
          />
        </Field>
        <Field label="Organization" htmlFor="company" error={errors.company?.message}>
          <Input
            id="company"
            autoComplete="organization"
            aria-invalid={!!errors.company}
            aria-describedby={errors.company ? "company-error" : undefined}
            {...register("company")}
          />
        </Field>
        <Field label="Role" htmlFor="role" error={errors.role?.message}>
          <Input
            id="role"
            autoComplete="organization-title"
            aria-invalid={!!errors.role}
            aria-describedby={errors.role ? "role-error" : undefined}
            {...register("role")}
          />
        </Field>
      </div>
      <Field label="Industry" htmlFor="industry" error={errors.industry?.message}>
        <Select
          id="industry"
          aria-invalid={!!errors.industry}
          aria-describedby={errors.industry ? "industry-error" : undefined}
          {...register("industry")}
        >
          <option value="">Select an industry</option>
          {industries.map((i) => (
            <option key={i.slug} value={i.title}>
              {i.title}
            </option>
          ))}
          <option value="Other">Other</option>
        </Select>
      </Field>
      <Field label="How can we help?" htmlFor="message" error={errors.message?.message}>
        <Textarea
          id="message"
          aria-invalid={!!errors.message}
          aria-describedby={errors.message ? "message-error" : undefined}
          {...register("message")}
        />
      </Field>
      <div className="absolute -start-[9999px]" aria-hidden>
        <label htmlFor="website">Website</label>
        <input id="website" tabIndex={-1} autoComplete="off" {...register("website")} />
      </div>
      <div>
        <div className="flex gap-3">
          <Controller
            control={control}
            name="consent"
            render={({ field }) => (
              <Checkbox
                id="consent"
                checked={field.value}
                onCheckedChange={(c) => field.onChange(c === true)}
                aria-describedby={errors.consent ? "consent-error" : undefined}
              />
            )}
          />
          <label htmlFor="consent" className="text-sm text-slate-700">
            I agree that Atlas Screening may contact me about this request, as described in the
            privacy policy.
          </label>
        </div>
        {errors.consent ? (
          <p id="consent-error" role="alert" className="mt-1.5 text-caption text-danger">
            {errors.consent.message}
          </p>
        ) : null}
      </div>
      {error ? (
        <p
          role="alert"
          className="rounded-control border border-danger/40 bg-[#f8e3e0] px-4 py-3 text-sm text-[#7a2218]"
        >
          {error}
        </p>
      ) : null}
      <Button type="submit" size="lg" disabled={isSubmitting}>
        {isSubmitting ? "Sending" : "Request a demo"}
      </Button>
    </form>
  );
}
