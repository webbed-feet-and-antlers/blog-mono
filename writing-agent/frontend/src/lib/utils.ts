import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Deterministic small jitter in [-0.25, 0.25] so scatter points don't
 * reshuffle between renders. */
export function jitter(label: string): number {
  let h = 0;
  for (const c of label) h = (h * 31 + c.charCodeAt(0)) % 997;
  return ((h % 100) / 100) * 0.5 - 0.25;
}
