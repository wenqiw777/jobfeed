import type { Config } from "tailwindcss";

/**
 * DESIGN.md ("Graphite & Cobalt") as the Tailwind theme.
 *
 * Colors REPLACE the default palette (not extend) so the only color
 * vocabulary available in class names is the token set — the default
 * grays/ambers/gradient-bait are unreachable by construction, which is
 * how the absolute bans (warm-cream bg, decorative color) stay enforced.
 * Every value points at a semantic CSS variable (src/styles.css) so a
 * future dark theme swaps variables, not classes. The variables hold
 * bare RGB channels and the theme wraps them in
 * `rgb(... / <alpha-value>)` so alpha modifiers (`bg-ink/30`) compile
 * to real CSS instead of silently emitting nothing.
 */
function token(name: string): string {
  return `rgb(var(--${name}) / <alpha-value>)`;
}

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",
      bg: token("bg"),
      surface: token("surface"),
      border: { DEFAULT: token("border"), strong: token("border-strong") },
      hairline: token("hairline"),
      ink: { DEFAULT: token("ink"), 2: token("ink-2") },
      mute: token("mute"),
      faint: token("faint"),
      accent: {
        DEFAULT: token("accent"),
        hover: token("accent-hover"),
        bg: token("accent-bg"),
        border: token("accent-border"),
      },
      apply: { DEFAULT: token("apply"), bg: token("apply-bg") },
      consider: { DEFAULT: token("consider"), bg: token("consider-bg") },
      skip: { DEFAULT: token("skip"), bg: token("skip-bg") },
      danger: {
        DEFAULT: token("danger"),
        hover: token("danger-hover"),
        bg: token("danger-bg"),
      },
    },
    fontFamily: {
      sans: ["Geist", "-apple-system", "system-ui", "Segoe UI", "sans-serif"],
      mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "monospace"],
    },
    // Fixed rem steps (~1.2 ratio), per DESIGN.md typography table.
    fontSize: {
      h1: ["1.3125rem", { lineHeight: "1.3", fontWeight: "600" }], // 21px
      h2: ["1.125rem", { lineHeight: "1.35", fontWeight: "600" }], // 18px
      h3: ["0.9375rem", { lineHeight: "1.4", fontWeight: "600" }], // 15px
      body: ["0.875rem", { lineHeight: "1.5" }], // 14px
      "body-sm": ["0.8125rem", { lineHeight: "1.45" }], // 13px
      compact: ["0.78125rem", { lineHeight: "1.4" }], // 12.5px
      label: ["0.75rem", { lineHeight: "1.35", fontWeight: "500" }], // 12px
      micro: ["0.6875rem", { lineHeight: "1.3" }], // 11px
    },
    extend: {
      // Radii: controls 6px, panels/cards 8–10px, pills 99px.
      borderRadius: {
        control: "6px",
        panel: "8px",
        "panel-lg": "10px",
        pill: "99px",
      },
      boxShadow: { 1: "var(--shadow-1)" },
      height: {
        "row-compact": "var(--row-compact)",
        "row-comfortable": "var(--row-comfortable)",
      },
      // Semantic z scale: dropdown < sticky < drawer-backdrop < drawer
      // < dialog < toast < tooltip.
      zIndex: {
        dropdown: "30",
        sticky: "40",
        "drawer-backdrop": "50",
        drawer: "60",
        dialog: "70",
        toast: "80",
        tooltip: "90",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-out": { from: { opacity: "1" }, to: { opacity: "0" } },
        "pop-in": {
          from: { opacity: "0", transform: "scale(0.98)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "slide-in-right": {
          from: { transform: "translateX(100%)" },
          to: { transform: "translateX(0)" },
        },
        "slide-out-right": {
          from: { transform: "translateX(0)" },
          to: { transform: "translateX(100%)" },
        },
        // Decided triage rows collapse before selection auto-advances.
        "row-collapse": {
          from: { opacity: "1", transform: "scaleY(1)" },
          to: { opacity: "0", transform: "scaleY(0)" },
        },
      },
      // 150–250ms ease-out only; no bounce/elastic (DESIGN.md motion).
      animation: {
        "fade-in": "fade-in 150ms cubic-bezier(0.22, 1, 0.36, 1)",
        "fade-out": "fade-out 150ms cubic-bezier(0.22, 1, 0.36, 1)",
        "pop-in": "pop-in 180ms cubic-bezier(0.22, 1, 0.36, 1)",
        "slide-in-right": "slide-in-right 220ms cubic-bezier(0.22, 1, 0.36, 1)",
        "slide-out-right": "slide-out-right 180ms cubic-bezier(0.22, 1, 0.36, 1)",
        "row-collapse": "row-collapse 180ms cubic-bezier(0.22, 1, 0.36, 1) forwards",
      },
    },
  },
  plugins: [],
} satisfies Config;
