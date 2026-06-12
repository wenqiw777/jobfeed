import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge must learn the custom type scale: without this,
 * `text-h1`/`text-micro` are misread as text *colors* and a later
 * `text-ink` would silently drop the size class (and vice versa).
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        { text: ["h1", "h2", "h3", "body", "body-sm", "compact", "label", "micro"] },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
