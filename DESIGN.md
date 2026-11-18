---
name: EasyCanvas
description: "A quiet, dense, near-monochrome workspace for real Canvas coursework."
colors:
  background: "#ffffff"
  foreground: "#16181d"
  foreground-secondary: "#3f4452"
  surface: "#ffffff"
  border: "#e6e7ec"
  border-strong: "#cdd0d9"
  control-border: "#858a99"
  muted: "#5b6270"
  accent: "#4f46e5"
  accent-hover: "#4338ca"
  accent-soft: "#eef0ff"
  sidebar: "#fafafb"
  table-heading: "#f7f7f9"
  row-hover: "#f6f6f9"
  nav-hover: "#efeff3"
  danger: "#b42318"
  danger-hover: "#912018"
  course-default: "#8975be"
  course-blue: "#668bb9"
  course-green: "#64958a"
  course-amber: "#a78b5a"
typography:
  fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
  page-title: { fontSize: "20px", fontWeight: 600, lineHeight: 1.3, letterSpacing: "-0.015em" }
  summary-value: { fontSize: "24px", fontWeight: 600, lineHeight: 1.25, letterSpacing: "-0.02em" }
  section-title: { fontSize: "16px", fontWeight: 600, lineHeight: 1.4, letterSpacing: "-0.01em" }
  body: { fontSize: "14px", fontWeight: 400, lineHeight: 1.5 }
  reading: { fontSize: "15px", fontWeight: 400, lineHeight: 1.8 }
  secondary: { fontSize: "13px", fontWeight: 400, lineHeight: 1.5 }
  meta: { fontSize: "12px", fontWeight: 500, lineHeight: 1.5 }
  button-label: { fontSize: "13px", fontWeight: 500, lineHeight: 1.5 }
  navigation: { fontSize: "14px", fontWeight: 500, lineHeight: 1.5 }
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "8": "32px"
sizing:
  control-height: "32px (44px on phones)"
  sidebar-width: "224px (208px at 768-1100px)"
  page-max-width: "1200px"
  page-gutter: "32px (24px tablet, 16px phone)"
components:
  button-secondary: { backgroundColor: "{colors.surface}", textColor: "{colors.foreground}", border: "{colors.border-strong}", rounded: "{rounded.md}", height: "32px" }
  button-primary: { backgroundColor: "{colors.accent}", textColor: "#ffffff", rounded: "{rounded.md}", height: "32px" }
  button-danger: { backgroundColor: "{colors.danger}", textColor: "#ffffff", rounded: "{rounded.md}", height: "32px" }
  navigation: { textColor: "{colors.foreground-secondary}", typography: "{typography.navigation}", rounded: "{rounded.md}", height: "32px" }
  navigation-active: { backgroundColor: "{colors.accent-soft}", textColor: "{colors.accent-hover}" }
  coursework-panel: { backgroundColor: "{colors.surface}", border: "{colors.border}", rounded: "{rounded.lg}" }
  table-heading: { backgroundColor: "{colors.table-heading}", textColor: "{colors.muted}", padding: "8px 16px" }
  table-cell: { padding: "10px 16px" }
---

# Design System: EasyCanvas

## Overview

**Creative North Star: "The Quiet Workspace"**

EasyCanvas is a dense, calm, near-monochrome tool for understanding coursework. It takes cues from Linear, Notion and Vercel (clean type, hairline borders, compact rows, one restrained accent) without copying any of them. Real Canvas data is the visual subject; the interface stays out of its way.

This file records the approved visual system (light theme). `PRODUCT.md` and `AGENTS.md` remain the authority for product behavior and architecture.

**Key Characteristics:**

- White surfaces, hairline borders, no shadows, no gradients, no decorative color.
- One indigo accent, used only for the primary action, the active page and keyboard focus.
- Small, disciplined type: page titles are 20px, not display-sized.
- Compact rows (about 40px) and a 4px spacing grid.
- Semantic tables with explicit data states.
- Dark mode is deliberately deferred.

### Rollout status

The visual system is being applied incrementally. **Phase 1 (done):** tokens, type scale, buttons, page headers, desktop sidebar and mobile navigation. **Phase 2 (done):** Dashboard and Priorities layouts and the shared row pattern. **Next:** Upcoming, Courses, Class Schedule, Study Assistant and Settings. The page-specific sections further down (Courses, Upcoming, Class Schedule, Study Assistant) still describe the earlier page layouts and will be revised as each page is redesigned; where they conflict with the foundation rules above and below, the foundation rules win.

### Purpose

This file defines the product design rules for EasyCanvas.

All coding agents must read this file before making meaningful frontend changes.

The goal is to keep EasyCanvas visually consistent, useful, and intentional. The interface should feel like a serious academic productivity product, not a generic AI-generated SaaS dashboard.

### Product Design Goal

EasyCanvas should feel:

- calm
- focused
- academic
- modern
- trustworthy
- efficient
- easy to scan
- useful with realistic amounts of student data

The interface should prioritize clarity, hierarchy, and usability over decoration.

The visual design should support the product's core promise:

> Help students understand what matters, what to work on, and what their courses require.

### Design Inspiration

Linear, Notion and Vercel: clean typography, restrained color, compact scannable layouts and consistent components. Do not copy their branding or layouts. Avoid excessive cards, shadows, colored stripes, oversized headings, gradients and decorative elements.

Keep the existing EasyCanvas logo files in their original colors and the seven navigation labels. This direction does not authorize new product capabilities or invented academic data.

## Colors

The palette pairs white and barely-tinted neutral surfaces with dark readable type and one restrained indigo accent. The frontmatter carries the exact values.

### Primary

Indigo (`accent`, `#4f46e5`) is used for exactly three things: the one filled primary action in a view, the active navigation item (as `accent-soft` fill with `accent-hover` text), and keyboard focus rings. It is not used for body links, list titles, badges or decoration. Text links are neutral (`foreground`) with a hairline underline that darkens on hover.

### Neutral

`background` and `surface` are white. `sidebar`, `table-heading` and `row-hover` are barely tinted greys. `foreground` carries primary text, `foreground-secondary` navigation and secondary controls, `muted` supporting text (6.1:1 on white). `border` (hairline) divides panels, rows and regions; `border-strong` outlines buttons; `control-border` outlines form controls and meets 3:1 against white (WCAG 1.4.11). Hover colors are quiet local state changes.

### Semantic

`danger` (red) means overdue, missing, error or a destructive action. Amber is reserved for "due soon" and is not yet tokenised. Green means confirmed or done. Color is never the only signal.

### Course identification

The default, blue, green, and amber course colors belong to small identity dots only. They are not priority scores or semantic states. Left-edge color stripes on rows and cards are being removed page by page (Phase 2 onward) because they are mistaken for severity.

**The Quiet Accent Rule.** One indigo accent, three jobs: primary action, active page, focus. Everything else is neutral.

### Color System

Use a restrained color system.

Recommended structure:

#### Neutral colors

Use neutral colors for:

- application background
- surfaces
- borders
- secondary text
- muted states

#### Primary accent

Indigo for the primary action, active navigation and focus only. Routine buttons stay neutral.

Do not use the accent color on every component.

#### Semantic colors

When semantic states are implemented, use semantic colors consistently; these are role guidelines, not evidence of shipped semantic color tokens:

- red: error, overdue, critical urgency
- amber/orange: warning, approaching deadline
- green: success, completed, confirmed
- blue or primary accent: neutral informational emphasis

Color must not be the only method used to communicate state.

### Course Colors

Courses may use a small identifying color if useful.

Examples:

- narrow left border
- small dot
- subtle badge
- icon accent

Do not turn the entire course card into a bright course-specific color.

Course colors should help recognition, not dominate the interface.

## Typography

**Application Font:** Locally served Inter variable, with Helvetica Neue, Arial, and sans-serif fallbacks. `--font-inter` is configured with `next/font/local`; weights span 100–900.

### Type scale

Inter only. Sizes: **12** meta and table headings, **13** secondary text and buttons, **14** body, tables and navigation, **15** long-form reading (assistant answers), **16** section titles, **20** page titles, **24** summary numbers. Weights: **400, 500, 600** only (no half-steps). Tabular numerals for dates and counts. Page titles use -0.015em tracking; nothing uses heavy negative tracking. Page descriptions are 13px `muted`.

Phones keep the same sizes; control and navigation heights grow instead (see Layout). Do not add a decorative or monospace display voice.

### Typography

Use a clean sans-serif application font.

Use the locally served Inter variable font configured in `frontend/app/layout.tsx`; its license is retained in `frontend/app/fonts`. The existing Geist Mono configuration is not the visible application text voice.

Hierarchy should be obvious:

- page title
- section title
- card/list title
- body
- metadata
- helper text

Rules:

- Avoid excessive all-caps.
- Avoid tiny low-contrast text.
- Avoid overly bold body copy.
- Avoid multiple unrelated fonts.
- Keep AI responses and long academic content highly readable.
- Use consistent line height and spacing.

Typography should do more of the hierarchy work so color does not have to.

## Layout

### Grid, spacing and breakpoints

A 4px grid with the scale 4, 8, 12, 16, 20, 24, 32 (`--space-*`). The desktop shell fills `100dvh` with a 224px sidebar and an independently scrolling main area; content is centered at a maximum of 1200px with 24px vertical and 32px horizontal padding. Page headers are one compact block (20px title, optional 13px description) with actions on the right and 20px below. Panels are separated by 16px. Table rows are about 40px (10px/16px cell padding).

At 768 to 1100px the sidebar is 208px and the gutter 24px. At 767px and below the sidebar becomes a 56px top bar with a **Menu** button that opens the full list of seven destinations as an overlay; the gutter is 16px and every interactive control is at least 44px tall. Container queries still reflow tables to labelled stacked rows at 720px; a denser two-line mobile row pattern is planned with the Dashboard and Priorities redesign.

### Overall Layout

Desktop is the primary target.

Use a persistent left sidebar and a consistent main content area.

Typical application structure:

```text
┌───────────────┬───────────────────────────────────────────┐
│               │ Page title                                │
│   Sidebar     │ Supporting text / actions                  │
│               │                                           │
│               │ Main content                               │
│               │                                           │
└───────────────┴───────────────────────────────────────────┘
```

Pages should use the same content width, alignment, and spacing rhythm.

Avoid large empty hero sections inside the authenticated application.

Important academic information should appear above the fold when practical.

### Spacing

Use consistent spacing increments.

Prefer a small spacing scale rather than arbitrary values everywhere.

Examples of intended rhythm:

- tight spacing for metadata
- moderate spacing inside components
- larger spacing between sections
- consistent page padding

Avoid:

- random Tailwind spacing values on every component
- extremely large empty gaps
- compressed dense layouts with no breathing room

The page should feel organized, not sparse or crowded.

### Tables and Dense Information

Use tables when comparing structured data benefits from columns.

Use lists when reading order matters more than comparison.

Avoid:

- horizontal scrolling for normal desktop content
- excessive column counts
- tiny metadata
- tables inside cards inside cards

For responsive layouts, convert tables into stacked rows when appropriate.

### Responsive Behavior

Desktop is the primary project target.

Still ensure:

- layouts do not break at narrower widths
- lists wrap sensibly
- forms remain usable
- content does not overflow unnecessarily
- sidebar behavior is intentional
- dialogs fit smaller screens

Do not spend significant project time on advanced mobile polish before the desktop experience is complete.

## Elevation & Depth

The interface is flat: no shadows or gradients. White panels are separated from the very light neutral page by subtle borders. Pale table headers and active navigation provide quiet tonal distinction without elevation.

**The Shared Surface Rule.** Keep surfaces flat. Use subtle borders for panel boundaries, row separators, and summary dividers; do not add shadows or gradients.

## Shapes

### Borders, Radius, and Shadows

Three radii only: **4px** (small labels), **6px** (buttons, controls, notices, nav items), **8px** (panels and dialogs). Pills (999px) and circles are limited to course dots, switches and badges. One-pixel hairline borders; no shadows or gradients. Do not nest rounded containers or wrap every region in a card.

## Components

### Observed shared patterns

**Buttons** are 32px tall (44px on phones), 13px/500, 6px radius. Variants: **secondary** (white, `border-strong`; the original `.primary-button` class, kept for compatibility), **primary** (`.btn-primary`, filled indigo; at most one per view), **ghost** (`.btn-ghost`) and **danger** (`.btn-danger`). Disabled buttons use a grey fill and `muted` text. **Form controls** share the 32px height, 6px radius, `control-border` and a 2px indigo focus ring. **Text links** are neutral with a hairline underline.

`AssignmentTable` is the shared presentation component for assignment rows; `formatCourseCode` supplies the readable course label. Pages keep responsibility for fetching and status messages.

Focus is a 2px indigo outline offset 2px on every interactive element, including selects, inputs, summaries and buttons. Transitions last 150ms and respect reduced motion. Reuse the original EasyCanvas logo files without a grayscale filter.

### Sidebar

Primary navigation:

- Dashboard
- Courses
- Upcoming
- Priorities
- Class Schedule
- Study Assistant
- Settings

Unless a route is intentionally not yet implemented, these items define the target sidebar structure. If a required item is omitted from a frontend task, the agent must explicitly call out that omission in the Design Compliance Check.

Rules:

- Keep the sidebar visually stable across pages.
- Make the active route obvious but restrained.
- Use a pale indigo fill (`accent-soft`) with `accent-hover` text for the active item; keep the other states neutral.
- Use consistent icons.
- Keep labels visible on normal desktop widths.
- Do not use gradients as the default sidebar treatment.
- Keep the seven existing labels. Unimplemented destinations remain visibly disabled with a `Soon` label; do not imply their features exist.
- Avoid oversized branding that wastes vertical space: a 24px mark and a 16px wordmark.
- Desktop: 224px wide, 32px items, 16px icons, Settings set apart under a hairline directly after the page links (not pinned to the bottom).
- Phones: a 56px top bar with a **Menu** button (`aria-expanded`, `aria-controls`). The panel lists all seven destinations in 48px rows, closes on Escape (focus returns to the button), on scrim click, and whenever the route changes.
- Each destination has a distinct icon.

The sidebar should feel like navigation, not decoration.

### Cards and Lists

Use cards only when a group of information benefits from being visually contained.

Good uses:

- course summary
- schedule block
- important summary widget
- grouped assistant context

For assignments and upcoming work, prefer structured rows or compact list items when possible.

A useful assignment row should make these easy to scan:

- assignment title
- course
- due date
- status
- importance or priority
- relevant action

Do not create huge cards for every assignment.

### Buttons

Use clear action hierarchy.

#### Primary

One visually dominant action per local context when possible.

Examples:

- Ask EasyCanvas
- Generate Study Plan
- Confirm Schedule
- Add Class Time

#### Secondary

Use for supporting actions.

Examples:

- Edit
- Refresh
- View Course

#### Tertiary

Use text or subtle buttons for low-priority actions.

Avoid:

- several equally dominant buttons side by side
- vague labels such as "Go"
- giant buttons for routine operations
- gradient buttons by default

Button text should describe the action.

### Forms

Forms should remain simple.

Rules:

- Use visible labels.
- Do not rely only on placeholders.
- Show validation near the relevant field.
- Group related fields.
- Use sensible defaults.
- Do not ask for information EasyCanvas already has.
- Explain why unusual information is required.

Manual class schedule entry should be straightforward and quick.

### Loading States

Never show fake final values while data is loading.

Bad:

```text
Upcoming Assignments: 0
```

when EasyCanvas is still fetching data.

Better:

```text
Loading upcoming assignments...
```

Useful loading messages may include:

- Loading your courses...
- Syncing Canvas...
- Finding course materials...
- Processing lecture slides...
- Searching CSE 3380 materials...

Use skeletons or simple progress indicators when appropriate.

Do not over-animate loading states.

### Empty States

Empty states should explain both the situation and the next action.

Examples:

> No upcoming assignments found.

> EasyCanvas could not find a class schedule for this course. Add the meeting time manually.

> No course materials have been processed yet.

Avoid empty screens with no explanation.

### Error States

Errors should be understandable and actionable.

Example:

> EasyCanvas could not refresh this course from Canvas. Your previous data is still available.

Do not expose:

- stack traces
- access tokens
- raw authorization headers
- internal backend exceptions
- unnecessary API implementation details

Error states should distinguish between:

- authentication failure
- unavailable Canvas feature
- network failure
- no data
- processing failure

### Dashboard

The Dashboard should quickly answer:

- What courses am I taking?
- What is coming up?
- What needs attention?
- What should I do next?

Keep summary information compact.

Do not place every feature on the dashboard.

The dashboard should show a concise preview of upcoming work rather than duplicating the full Upcoming page. When a longer upcoming list exists elsewhere, prefer a short preview plus a clear action such as `View all upcoming`.

Implemented layout (Phase 2): a one-line header, a three-number **stat strip** framed by hairlines (due within 24 hours, due in 1-3 days, all upcoming), then two columns. The wide left column is **Upcoming Assignments**: the next eight deadlines grouped by urgency (due within 24 hours, due in 1-3 days, later), each a `.work-row`, with `View all upcoming`. The 320px right column holds **Next class** and a compact **Courses** list (code, cleaned course name, upcoming count; each row links to the course page). Below 900px the columns stack with Next class first. There are no tables, cards or per-row course colors on the Dashboard. Overdue work is not shown here because the Dashboard still reads only the existing upcoming-assignments data; adding it means reading the Priorities data and is a separate decision.

Avoid decorative dashboard widgets with no clear student value.

### Courses

Courses should provide a clear entry into course-specific context.

A course page may eventually include:

- assignments
- upcoming work
- course materials
- syllabus information
- course-specific Study Assistant
- ingestion/sync status

When the student enters a course, the active course should remain obvious.

Course pages should not feel like unrelated mini-apps.

### Upcoming

Upcoming should be optimized for scanning.

Each item should clearly show:

- assignment
- course
- due date and time
- submission status
- useful importance information when available

Sorting should be obvious.

Avoid making the user open separate course pages just to understand workload.

### Priorities

The Priorities page shows ranked work and explains why. Ranking is deterministic application logic; AI never invents it.

Implemented presentation (Phase 2): a four-number stat strip, one toolbar row (labels beside their controls), then unboxed sections (Overdue, Upcoming, No due date, Not actionable, Completed). Each section is a header rule with the title, count and a short note, followed by `.work-row` rows. A row is the assignment title (neutral, a link that opens Canvas in a new tab), a muted line `course · status`, and on the right a compact date (`Thu, Nov 19 · 11:59 PM`, full date and zone in the tooltip) with the relative label (`Due tomorrow`).

Rules:

- No left stripes, no priority pills. Overdue is the section plus red due text; `Missing` is red text; High and Medium appear as the plain words "High priority" / "Medium priority" (amber); Low is announced to screen readers only.
- Red means overdue, missing or an error. Amber means due soon. Nothing else is colored.
- If a numeric score is ever shown, give plain-English reasons; avoid unexplained percentages.

### Shared row pattern (`.work-row`)

Two-column grid: title and meta on the left, due date and relative label on the right, hairline between rows, 8px/12px padding, hover fill. On phones it stacks to title, meta, then date and relative text on one line. Use it for any list of assignments; do not wrap it in a card.

### Class Schedule

The Class Schedule page should support:

- automatically discovered meeting times
- syllabus-extracted meeting times
- manually entered meeting times
- editing and confirmation

The source should be visible when useful:

- Canvas
- syllabus
- manual

Automatically extracted schedule data should be confirmed before being treated as trusted planning data.

If extraction is running, show progress.

If no schedule is found, manual entry must be obvious.

The schedule should eventually act as fixed unavailable time for study planning.

### Study Assistant

The Study Assistant should look like EasyCanvas, not a ChatGPT clone.

Course context should always be obvious.

Example:

```text
CSE 3380
Linear Algebra

Ask about this course...
```

The assistant may support:

- lecture-slide questions
- assignment explanations
- syllabus questions
- course concepts
- study preparation
- what to review for an assignment
- contextual study guidance

When appropriate, responses should show supporting course sources.

Examples:

- Lecture 7: Determinants
- Homework 4 Instructions
- Course Syllabus

Do not invent source labels.

Visually distinguish:

- student message
- EasyCanvas response
- referenced course sources
- structured Canvas facts

Avoid fake typing animations, glowing AI borders, magic sparkles, or decorative AI effects unless they add real usability.

### AI Entry Points

EasyCanvas can use one underlying AI system through different product surfaces.

#### Priorities

Question:

> What should I work on next?

Uses:

- assignment data
- deterministic priority engine
- schedule data

#### Study Assistant

Question:

> Explain this assignment.

Uses:

- active course
- structured assignment information
- retrieved course materials
- LLM

#### Study Planning

Question:

> I have three free hours tonight. What should I do?

Uses:

- priorities
- deadlines
- confirmed class schedule
- availability
- estimated effort

The interfaces may differ, but the product should feel like one coherent system.

### Dialogs and Modals

Use dialogs for focused temporary tasks, such as:

- adding class time
- confirming extracted schedule information
- editing a setting

Do not use modals for long workflows that deserve their own page.

Keep modal actions obvious.

### Motion

Use motion sparingly.

Good uses:

- subtle menu transitions
- lightweight loading feedback
- small state transitions

Avoid:

- animated backgrounds
- bouncing UI
- constant pulsing
- dramatic page transitions
- animation added simply because the feature uses AI

Functionality must not depend on animation.

The current build uses short color/background state transitions (150ms) and removes transitions for `prefers-reduced-motion: reduce`. Do not add entrance choreography as part of this theme.

### Component Reuse

Prefer reusable components for repeated patterns such as:

- sidebar navigation
- course roster rows
- assignment tables
- status badges
- priority indicators
- loading states
- empty states
- dialogs
- buttons
- form controls

Reuse should improve consistency and understanding.

Do not create abstractions only because two pieces of markup happen to look similar.

## Do's and Don'ts

### Do:

- Do preserve real Canvas facts, timestamps, loading, empty, error, and partial-coverage states.
- Do reuse local Inter, the neutral palette, subtle borders, consistent spacing, and compact corners.
- Do keep course accents small and stable; retain visible context and readable deadlines.
- Do preserve meaningful labels, keyboard focus, contrast, and non-color state cues.
- Do inspect desktop and narrow rendered layouts before giving visual approval.

### Don't:

- Don't copy the reference pricing composition into the academic application.
- Don't add a marketing hero, invented academic data, or unimplemented feature claims.
- Don't use gradients, shadows, glass decoration, glowing AI effects, neon accents, or emoji icons.
- Don't nest rounded cards, turn every row into a card, or use course colors as full-card fills.
- Don't treat source checks, HTTP success, or a passing build as rendered visual approval.

### Anti-AI-Slop Rules

Avoid common AI-generated frontend patterns unless they have a real usability purpose.

Do not default to:

- purple gradients
- neon accents
- glowing cards
- glassmorphism everywhere
- decorative blobs
- abstract background shapes
- giant hero headings inside the application
- random emoji
- excessive shadows
- oversized rounded cards
- every card having a different pastel color
- unnecessary animated gradients
- generic "AI magic" visual effects
- dashboard layouts that look copied from SaaS templates
- wrapping every piece of information in a separate card

A visually attractive screenshot is not enough.

Frontend work should be judged by:

- clarity
- consistency
- information hierarchy
- realistic data behavior
- accessibility
- maintainability
- usefulness to students

### Existing Interface First

Before changing the UI:

1. Read `AGENTS.md`.
2. Read `DESIGN.md`.
3. Run the existing frontend.
4. Inspect the current page and shared components.
5. Identify existing layout, spacing, typography, navigation, and component patterns.
6. Explain the proposed visual change before making a large redesign.

Preserve useful existing patterns.

Do not redesign unrelated pages while implementing one feature.

Do not introduce a second design system.

Do not change the app's visual direction without developer approval.

### Accessibility

Maintain:

- semantic HTML where practical
- sufficient color contrast
- visible keyboard focus
- keyboard-accessible controls
- meaningful form labels
- meaningful button text
- appropriate click/touch targets

Do not communicate important information through color alone.

### Design Tokens

Where practical, centralize commonly reused values:

- brand color
- neutral colors
- spacing
- border radius
- text sizes
- surface colors

Prefer consistent Tailwind classes or project-level variables over arbitrary one-off values.

Do not build an elaborate design-token system solely for theoretical cleanliness.

### Realistic Data Rule

UI must be tested with realistic student data.

Do not judge a component only with:

- one short assignment
- one course
- placeholder names
- ideal empty data

Test with:

- long course names
- many assignments
- missing due dates
- long assignment titles
- several courses
- loading states
- errors
- no upcoming work

The design should remain usable under real academic workloads.

### Frontend Agent Workflow

Before implementing a visual change:

1. Read `AGENTS.md`.
2. Read `DESIGN.md`.
3. Run the current frontend.
4. Inspect the rendered UI.
5. Inspect relevant existing components.
6. Explain what currently works visually.
7. Explain what should change.
8. Identify the files that need edits.
9. Make the smallest coherent change.
10. Run the frontend again.
11. Inspect the result.
12. Check for regressions, console errors, broken states, and inconsistent styling.
13. Explain exactly what changed.

Do not declare frontend work complete simply because it compiles.

When visual inspection is available, inspect the rendered application rather than judging frontend quality only from source code.

### Design Change Approval Rule

For meaningful redesigns, do not immediately rewrite the frontend.

First present:

- current design problems
- proposed direction
- components/pages affected
- what will remain unchanged
- expected visual result

Then wait for developer approval before implementing the larger redesign.

Small fixes that clearly follow the established design system may be implemented directly.

### Design Compliance Check

Before considering any frontend redesign or visual task complete, compare the implemented result against every relevant requirement in this file.

The agent must explicitly report:

- which relevant `DESIGN.md` requirements were implemented
- which relevant requirements were intentionally not implemented
- why any requirement was skipped
- whether the skipped item is out of scope, blocked, or deferred
- whether the current task introduced any new visual pattern not already defined here

Do not silently omit a relevant design requirement.

Example:

> `Class Schedule` is listed in the required sidebar navigation, but this task was limited to dashboard styling, so I did not add the route in this change.

This check is required even when the implementation request asks for the "smallest" or "most focused" redesign.

### Visual Review Checklist

Before considering a frontend task complete, check:

- Does this still look like EasyCanvas?
- Is the page easy to scan?
- Is the active course or context obvious?
- Is important information visually prioritized?
- Are colors purposeful rather than decorative?
- Are spacing and typography consistent?
- Are loading states handled?
- Are empty states handled?
- Are error states handled?
- Did this introduce an unnecessary visual pattern?
- Is the page usable with realistic student data?
- Are controls clearly labeled?
- Is the design accessible?
- Does this avoid generic AI/SaaS visual clichés?
- Did existing functionality remain intact?

### Final Design Principle

EasyCanvas should not try to impress users with visual effects.

It should impress them by making a complicated academic workload feel organized, understandable, and manageable.

The interface should look intentionally designed by its developers, not generated from a generic dashboard prompt.

### Verification and deferred review

This record was extracted from `frontend/app/globals.css`, `frontend/app/layout.tsx`, dashboard/Upcoming markup, `frontend/app/components/assignment-table.tsx`, the shared sidebar, and `frontend/lib/course-label.ts`, with direction from `.impeccable/surfaces/frontend-app-page-tsx.md`. No application code, font, logo, backend, API contract, stable identifier, or timestamp behavior is changed by this documentation.

The documentation tokens and component patterns are verified against source. Current implementation tests, lint, build, and route checks are reported separately by the implementation pass. No connected browser or desktop/mobile screenshots were available for this update. Rendered comparison with the newest user reference remains pending; source inspection and passing functional checks are not visual approval.

Existing `Sync Canvas` and course `View` affordances remain intentionally deferred implementation work: the former has no click handler and the latter is explicitly disabled non-interactive text. They are not canonical examples of completed controls. Do not extend their appearance as proof of working sync or course navigation. No new feature was added. Five planned destinations remain disabled with `Soon`; Dashboard and Upcoming remain available.
