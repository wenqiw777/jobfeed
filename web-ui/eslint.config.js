/**
 * Flat ESLint config: typescript-eslint recommended (the fast, untyped
 * variant — type-aware linting stays out of the hot path; tsc covers
 * types) + react-hooks recommended (rules-of-hooks error,
 * exhaustive-deps warn). No stylistic/formatting rules on purpose.
 */
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  // Build output and generated types are not lint targets.
  { ignores: ["dist", "src/api/types.gen.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
  },
  {
    files: ["scripts/**/*.mjs"],
    languageOptions: { globals: globals.node },
  },
);
