/**
 * DESIGN.md absolute-ban detector (no deps, offline).
 *
 * `npx impeccable detect` (the tool DESIGN.md's ban list cites) covers
 * .tsx files only via a narrow regex set, so this script is the durable
 * gate: it scans web-ui/src for the bans that can regress silently in
 * class strings — gradients, side-stripe borders, warm/cream color
 * classes, raw hex colors outside the token files, and content-area
 * spinner classes. Exit 1 on any finding.
 */
import { strict as assert } from "node:assert";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const scanRoot = resolve(root, process.argv[2] ?? "src");

const SCAN_EXTENSIONS = [".ts", ".tsx", ".css"];
/** Generated file — not authored UI code. */
const SKIP_FILES = new Set(["types.gen.ts"]);
/** The token sheet defines the palette, so its hex comments are legit. */
const HEX_EXEMPT_FILES = new Set(["styles.css"]);

const RULES = [
  {
    name: "gradient",
    pattern: /linear-gradient|radial-gradient|conic-gradient|bg-gradient-/g,
    why: "gradients are banned (DESIGN.md)",
  },
  {
    name: "side-stripe",
    pattern: /\bborder-[lr]-[24]\b/g,
    why: "side accent stripes are banned; selection is bg tint + inset ring",
  },
  {
    name: "warm-color-class",
    pattern: /\b(?:bg|text|border|from|via|to|ring)-(?:amber|yellow|orange|stone)-\d+\b/g,
    why: "warm/cream colors are banned; use the consider-family tokens",
  },
  {
    name: "raw-hex",
    // Only color-shaped lengths (3/4/6/8), and never an all-digit run —
    // issue refs like "#1234" and hex-letter words of non-color length
    // ("#beef202") are not findings. (Tradeoff: an all-digit color like
    // #112233 is also skipped; issue refs are far more common in comments.)
    pattern: /#(?![0-9]+\b)(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g,
    why: "colors come from tokens only (styles.css / tailwind.config.ts)",
    isExempt: (name) => HEX_EXEMPT_FILES.has(name),
  },
  {
    name: "spinner",
    pattern: /\banimate-spin\b/g,
    why: "content-area spinners are banned; use the Skeleton component",
  },
];

/**
 * Self-test, run before every scan: fixed must-flag / must-pass cases,
 * so a regex edit that breaks a rule fails `npm run check:design` itself
 * instead of regressing silently.
 */
const MUST_FLAG = [
  ["bg-[#fff]", "raw-hex"],
  ["background: linear-gradient(90deg, red, blue)", "gradient"],
  ["border-l-2", "side-stripe"],
  ["bg-amber-100", "warm-color-class"],
  ["animate-spin", "spinner"],
];
const MUST_PASS = [
  "fixes #1234", // all-digit issue ref, not a color
  "fixes #beef202", // hex-shaped word, but 7 is not a color length
  "#beef2", // 5 is not a color length either
];

function ruleNamesIn(text) {
  // Fresh regex per call: the /g patterns carry lastIndex state.
  return RULES.filter((rule) => new RegExp(rule.pattern.source).test(text)).map(
    (rule) => rule.name,
  );
}

function selfTest() {
  for (const [text, name] of MUST_FLAG) {
    assert(ruleNamesIn(text).includes(name), `self-test: [${name}] must flag ${JSON.stringify(text)}`);
  }
  for (const text of MUST_PASS) {
    assert.deepEqual(ruleNamesIn(text), [], `self-test: must not flag ${JSON.stringify(text)}`);
  }
}

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      yield* walk(path);
    } else if (SCAN_EXTENSIONS.some((ext) => entry.endsWith(ext)) && !SKIP_FILES.has(entry)) {
      yield path;
    }
  }
}

function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

selfTest();

const findings = [];
for (const path of walk(scanRoot)) {
  const name = path.split("/").at(-1);
  const text = readFileSync(path, "utf8");
  for (const rule of RULES) {
    if (rule.isExempt?.(name)) {
      continue;
    }
    for (const match of text.matchAll(rule.pattern)) {
      findings.push(
        `${relative(root, path)}:${lineOf(text, match.index)} [${rule.name}] "${match[0]}" — ${rule.why}`,
      );
    }
  }
}

if (findings.length > 0) {
  console.error(`${findings.length} design-ban finding(s):\n${findings.join("\n")}`);
  process.exit(1);
}
console.log(`check-design-bans: 0 findings in ${relative(root, scanRoot)}.`);
