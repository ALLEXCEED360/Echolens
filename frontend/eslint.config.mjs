import js from "@eslint/js";
import next from "eslint-config-next";
import tseslint from "typescript-eslint";

/**
 * `package.json` carried a `lint` script for months with no ESLint installed
 * and no config file, so `npm run lint` failed with "not recognized" rather
 * than linting anything. A script that has never run is worse than no script:
 * it reads as coverage that does not exist.
 */
export default tseslint.config(
  {
    // Build output, not source. `.next-build` is the alternate `distDir` from
    // next.config.ts; linting it produced 5,428 errors in minified vendor
    // bundles and buried the seven real ones.
    ignores: [
      ".next/**",
      ".next-build/**",
      "node_modules/**",
      "next-env.d.ts",
      "*.tsbuildinfo",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...next,
  {
    // `react-hooks/set-state-in-effect` comes from the React Compiler plugin
    // and reports past inline `eslint-disable-next-line` directives — worse,
    // `--fix` then deletes the directive as unused. So the two deliberate uses
    // are exempted here, by file, with their reasons.
    //
    //   EditableTitle  - re-syncs the draft when the title changes underneath
    //                    it. The alternative the rule wants is a `key`, which
    //                    remounts the input and drops focus mid-edit.
    //   collections    - loads on mount. `load` sets state before awaiting,
    //                    which is what the rule sees; fetching in an effect is
    //                    the point of the effect.
    files: ["components/EditableTitle.tsx", "app/collections/page.tsx"],
    rules: { "react-hooks/set-state-in-effect": "off" },
  },
  {
    rules: {
      // The codebase uses `_`-prefixed names for deliberately unused bindings.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
