# Cloudscape UI Redesign

## Purpose

Replace Jobfeed's hand-built Radix/Tailwind presentation layer with the
Cloudscape React design system while preserving every route, workflow, API
contract, keyboard shortcut, and SQLite-backed behavior. The result is a
coherent productivity console that a new user can configure and operate from
the browser without learning implementation details.

This plan is the source of truth for the UI redesign. It supersedes the visual
system and component-stack choices in Phase 8 decisions D3-D4, but does not
change Phase 8 or Phase 9 product behavior.

## Frozen design

- Use Cloudscape's toolbar application layout because Jobfeed is a dense,
  interactive work surface rather than a browsing or marketing experience.
- Use one application shell across all configured routes: top navigation,
  collapsible side navigation, route content, notifications, and contextual
  split panel.
- Preserve the Jobfeed identity through a restrained global theme: cobalt
  action color, graphite neutrals, Geist for UI text, and Geist Mono for scores,
  timestamps, counts, and identifiers.
- The signature interaction is a decision surface: collection on the left,
  resizable evidence/detail panel on the right. Triage must not regress to a
  generic table-only workflow.
- Keep Recharts during this migration. Chart replacement is not required to
  establish Cloudscape consistency and would add behavioral risk without a
  user benefit.
- Keep React Router, TanStack Query, API clients, generated types, and all
  backend code unchanged.

## Component mapping

| Current surface | Cloudscape target |
| --- | --- |
| Shell, sidebar, top bar | AppLayoutToolbar, TopNavigation, SideNavigation |
| Route title and actions | ContentLayout, Header |
| Job and run collections | Table/Cards, PropertyFilter/TextFilter, Pagination |
| Job details | SplitPanel |
| Setup | Wizard/Form, FormField, Input, Select, Alert |
| Status and verdicts | StatusIndicator, Badge |
| Dialogs and feedback | Modal, Flashbar |
| Dashboard panels | Container, Grid, existing Recharts |

## Milestones

### Milestone 1: foundation, setup, and triage

- [x] Install Cloudscape components, global styles, collection hooks, and the
  supported global theme.
- [x] Replace the global shell with AppLayoutToolbar, TopNavigation, and
  SideNavigation while preserving routes, badge counts, density, and keyboard
  hints.
- [x] Rebuild setup with Cloudscape form and feedback components.
- [x] Rebuild Triage around the Cloudscape decision surface while preserving
  query parameters, selection, bulk actions, keyboard commands, apply flow,
  follow-ups, notes, twins, pending-JD behavior, and auto-advance.
- [x] Record automated and Chrome evidence below.

### Milestone 2: operational routes

- [x] Rebuild Library, Sources, and Runs.
- [x] Remove Application Pipeline after the product decision below; retain
  probing, run triggers, SSE progress, filtering, pagination, and empty/error
  states.
- [ ] Record final automated and Chrome evidence below.

### Milestone 3: analytics and cleanup

- [x] Rebuild Insights and Performance page structure around Cloudscape.
- [ ] Remove unused Radix primitives, Tailwind component styling, Lucide icons,
  and obsolete custom UI wrappers after all callers have migrated.
- [ ] Keep only product-specific CSS and Recharts styling that Cloudscape does
  not own.
- [ ] Record automated and Chrome evidence below.

## Acceptance criteria

- All eight routes load through the canonical `jobfeed` launcher without
  Docker or PostgreSQL.
- Every existing frontend test remains green; changed interactions have
  test-first coverage using stable visible labels or `data-testid` selectors,
  never Cloudscape internal class names.
- `npm run build` and repository `make quality` pass.
- The production bundle under `web-ui/dist` is rebuilt.
- A real pass in the user's Chrome extension exercises every visible button,
  filter, dialog, drawer/panel, navigation item, and configuration field.
- Desktop and narrow viewport screenshots show no clipped actions, inaccessible
  panels, horizontal-page overflow, or mixed old/new component systems.
- No external network request or paid LLM call is issued during UI validation;
  manual fixtures are removed after the pass.

## Simplified decision model (frozen 2026-08-13)

- Application Pipeline is not a product zone. `/pipeline` remains only as a
  compatibility redirect to Triage.
- Triage is a filtered results surface with exactly three user decisions:
  **Applied**, **Wait**, and **Ignore**. Each is a lightweight status write;
  Applied does not read a resume, create an application audit, or open an
  upload/details dialog.
- Existing persisted workflow states remain readable for migration
  compatibility, but the UI groups them into the three decisions:
  shortlisted/awaiting-referral → Wait; applied/interviewing/offer/rejected/
  ghosted → Applied; ignored/archived → Ignored.
- Notes, follow-ups, JD paste, interview rounds, archive/restore, application
  artifacts, and pipeline status groups are no longer exposed in the GUI.
- Library uses the same decision vocabulary and is read-only; the backend
  compatibility APIs are outside this UI slice and are not deleted here.

## Evidence

- Baseline (2026-08-13): `npm test -- --run` — 21 files, 153 tests passed.
- Milestone 1 (2026-08-13): 153/153 frontend tests; production build;
  repository quality gate 1,872 passed / 418 deselected; Chrome extension
  verified the live Triage decision surface and full Settings form against the
  existing SQLite workspace.
- Milestone 2 simplification (2026-08-13): Pipeline zone and seven
  pipeline/application UI modules removed; Triage and Library expose only
  Results/Wait/Applied/Ignored; Applied/Wait/Ignore use lightweight status
  writes. Focused behavior tests were recorded RED, then GREEN. Final frontend
  verification: 19 files / 112 tests passed sequentially, lint/typecheck/build
  passed, production dependency audit reported 0 vulnerabilities. Repository
  quality gate: 1,872 passed / 418 deselected.
- Milestone 3 analytics slice (2026-08-13): 153/153 frontend tests;
  production build; design-ban check 0 findings. Cloudscape containers expose
  explicit region labels, window selectors retain their grouped semantics, and
  KPI values no longer introduce duplicate page-level headings.
- Final Chrome pass (user Chrome extension, 2026-08-13): Results, Wait,
  Applied, Ignored all selected and returned real SQLite-backed data/empty
  states; Library exposed the same four filters; `/pipeline` redirected to
  `/triage`; no Pipeline navigation, shortcuts, Shortlist/Skip/Restore/
  Interview controls, page-level horizontal overflow, or wrapped dates were
  present. Sample date computed `white-space: nowrap` with one-line height.
  Screenshot: `docs/evidence/2026-08-13-triage-decisions.png`.

## Finding disposition

- P0/P1 findings in a milestone block completion and receive a finding-only
  recheck.
- P2/P3 findings are recorded here for later work and do not restart a broad
  review.
- Frozen design decisions are revisited only with evidence of data loss or an
  infeasible implementation.
- P2 backlog: Cloudscape raises the eager application chunk from about 552 KB
  to 1.20 MB (350 KB gzip). Preserve route-level lazy loading and split the
  design-system vendor chunk during Milestone 3 cleanup.
