"use client";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { FormField } from "@/components/form-field";
import { ApiError, postSession } from "@/lib/api/client";
import {
  PASSWORD_MIN_LENGTH,
  fieldErrors,
  loginSchema,
  registerSchema,
  safeNextPath,
  type FieldErrors,
} from "@/lib/validation";

type Values = { display_name: string; email: string; password: string };

const COPY = {
  login: {
    title: "Log in",
    submit: "Log in",
    busy: "Logging in…",
    switchText: "New here?",
    switchLink: "Create an account",
    switchHref: "/register",
  },
  register: {
    title: "Create your account",
    submit: "Create account",
    busy: "Creating account…",
    switchText: "Already have an account?",
    switchLink: "Log in",
    switchHref: "/login",
  },
} as const;

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "rate_limited") return "Too many attempts. Wait a minute and try again.";
    return error.message;
  }
  return "Something went wrong. Try again.";
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const copy = COPY[mode];
  const router = useRouter();
  const next = safeNextPath(useSearchParams().get("next"));
  const [errors, setErrors] = useState<FieldErrors<Values>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget)) as Values;
    const parsed = (mode === "login" ? loginSchema : registerSchema).safeParse(data);
    if (!parsed.success) {
      setErrors(fieldErrors(parsed.error) as FieldErrors<Values>);
      setFormError(null);
      return;
    }
    setErrors({});
    setFormError(null);
    setBusy(true);
    try {
      if (mode === "register") await postSession("/api/session/register", parsed.data);
      await postSession("/api/session/login", {
        email: parsed.data.email,
        password: parsed.data.password,
      });
      router.replace(next as Route);
      router.refresh();
    } catch (error) {
      setFormError(messageFor(error));
      setBusy(false);
    }
  }

  return (
    <form
      // If the page's script has not loaded yet, a submit must never put the
      // password in the address bar (the default for a form is GET).
      method="post"
      onSubmit={onSubmit}
      noValidate
      className="space-y-5"
      aria-labelledby="auth-title"
    >
      <h1 id="auth-title" className="text-title">
        {copy.title}
      </h1>
      {formError ? (
        <p role="alert" className="border-l-4 border-unmet bg-surface px-3 py-2 font-medium">
          {formError}
        </p>
      ) : null}
      {mode === "register" ? (
        <FormField
          label="Your name"
          name="display_name"
          autoComplete="name"
          error={errors.display_name}
        />
      ) : null}
      <FormField
        label="Email"
        name="email"
        type="email"
        autoComplete="email"
        inputMode="email"
        error={errors.email}
      />
      <FormField
        label="Password"
        name="password"
        type="password"
        autoComplete={mode === "login" ? "current-password" : "new-password"}
        hint={mode === "register" ? `At least ${PASSWORD_MIN_LENGTH} characters.` : undefined}
        error={errors.password}
      />
      <button type="submit" className="button w-full" disabled={busy}>
        {busy ? copy.busy : copy.submit}
      </button>
      <p className="text-muted">
        {copy.switchText}{" "}
        <Link href={copy.switchHref} className="link">
          {copy.switchLink}
        </Link>
      </p>
    </form>
  );
}
