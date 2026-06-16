/**
 * The ATS vendor list, shared by the Sources forms and the probe flow.
 *
 * Built from a record `satisfies Record<CompanyVendor, true>` so the list
 * is checked exhaustive against the generated union: a 4th vendor landing
 * in the API types fails the build here instead of silently missing from
 * the vendor dropdown and the probe-result gate.
 */
import type { CompanyVendor } from "@/api/queries";

const VENDOR_SET = {
  greenhouse: true,
  ashby: true,
  lever: true,
} as const satisfies Record<CompanyVendor, true>;

export const VENDORS = Object.keys(VENDOR_SET) as readonly CompanyVendor[];

export function isVendor(value: string | null): value is CompanyVendor {
  return value !== null && (VENDORS as readonly string[]).includes(value);
}
