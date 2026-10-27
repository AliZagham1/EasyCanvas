---
name: EasyCanvas
description: "A calm academic workspace grounded in real Canvas coursework."
colors:
  background: "#fbfbfd"
  foreground: "#191b25"
  surface: "#ffffff"
  border: "#e1e4ec"
  muted: "#616879"
  accent: "#6941c6"
  accent-soft: "#eee9fb"
  sidebar: "#f6f6fa"
  table-heading: "#f5f6f9"
  row-hover: "#faf9fd"
  navigation-text: "#454b5d"
  navigation-hover: "#eeeff5"
  soon-border: "#d9dce6"
  course-default: "#8975be"
  course-blue: "#668bb9"
  course-green: "#64958a"
  course-amber: "#a78b5a"
typography:
  headline:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "38px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.035em"
  headline-tablet:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.035em"
  headline-mobile:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.035em"
  summary:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "32px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  section-title:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "20px"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
  table-body:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  assignment-title:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 450
    lineHeight: 1.5
  description:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.5
  metadata:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  button-label:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.5
  navigation:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 450
    lineHeight: 1.5
  navigation-active:
    fontFamily: "Inter, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 550
    lineHeight: 1.5
rounded:
  "surface": "8px"
  "navigation": "8px"
  "control": "6px"
  "small-label": "4px"
spacing:
  "4": "4px"
  "8": "8px"
  "12": "12px"
  "16": "16px"
  "20": "20px"
  "24": "24px"
  "28": "28px"
  "32": "32px"
  "36": "36px"
  "40": "40px"
components:
  button-primary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.navigation-text}"
    typography: "{typography.button-label}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
  button-primary-hover:
    backgroundColor: "{colors.table-heading}"
  navigation:
    textColor: "{colors.navigation-text}"
    typography: "{typography.navigation}"
    rounded: "{rounded.navigation}"
    padding: "11px 12px"
  navigation-active:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    typography: "{typography.navigation-active}"
  coursework-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.surface}"
  panel-header:
    padding: "16px 20px"
  summary-cell:
    padding: "0 24px"
  coverage-notice:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
  table-heading:
    backgroundColor: "{colors.table-heading}"
    textColor: "{colors.muted}"
    padding: "10px 20px"
  table-cell:
    typography: "{typography.table-body}"
    padding: "12px 20px"
---

# Design System: EasyCanvas

## Overview

**Creative North Star: "The Academic Workspace"**

EasyCanvas is a calm, focused academic productivity interface. White and very light neutral surfaces, subtle borders, and restrained lavender navigation keep real coursework prominent. Professional Inter typography and compact tables make courses, assignments, and whole deadlines easy to scan.

The latest user-supplied academic dashboard reference supersedes the earlier pricing-page theme. This system records durable visual rules from the current source, while `PRODUCT.md` and `AGENTS.md` remain the authority for product behavior and architecture.

**Key Characteristics:**

- White and very light neutral surfaces with subtle borders.
- Locally served Inter with a clear professional hierarchy.
- Lavender active navigation and restrained purple links.
- Flat panels without shadows or gradients.
- Semantic coursework tables with explicit data states.

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

The latest user-approved reference is a flat academic dashboard: white or very light neutral backgrounds, subtle borders instead of shadows, no gradients, calm professional typography, and a lavender active sidebar. This direction supersedes the earlier blue-gray pricing theme.

Keep the existing EasyCanvas brand assets and seven navigation labels. The current expression uses three compact summary regions with vertical dividers, a bordered Upcoming table, and a bordered Courses roster. It does not authorize new product capabilities or invented academic data.

The image has no local copy in this repository. This record is grounded in the current source and the supplied description; rendered comparison remains pending.

## Colors

The palette pairs white and very light neutral surfaces with dark readable type and a restrained lavender interaction accent. The frontmatter carries the exact source colors.

### Primary

Purple (`accent`) marks links, focus, and active navigation text. Pale lavender (`accent-soft`) supplies active navigation fill and text selection. Routine buttons remain white with a subtle border and muted dark text.

### Neutral

The very light `background` surrounds white `surface` panels. `sidebar` separates navigation; `table-heading` distinguishes column labels. `foreground` carries primary text, `muted` secondary text, and `border` divides panels, rows, and summary regions. Hover colors are quiet local state variations.

### Course identification

The default, blue, green, and amber course colors belong to small identity dots. They are not priority scores or semantic success/warning states.

**The Quiet Accent Rule.** Use the restrained purple accent for links and focus, and a pale lavender fill for active navigation. Keep routine controls neutral.

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

Use the restrained purple accent for links and keyboard focus, with pale lavender selected navigation and text selection. Keep routine buttons neutral.

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

### Observed hierarchy

- **Headline:** Page titles use 38px on desktop, 34px at tablet widths, and 28px on mobile.
- **Summary:** Tabular counts use 32px, falling to 28px on mobile; unavailable values retain a 16px state treatment.
- **Section title:** 20px group headings become 18px on mobile.
- **Table body:** 15px coursework text, with assignment titles at weight 450 and course codes at 500.
- **Body and description:** The application default is 14px; page descriptions use 16px, becoming 14px on mobile.
- **Metadata:** 13px for table headings, supporting course information, points, and notes.
- **Navigation and controls:** 14px navigation uses weight 450, active items 550; routine buttons use 13px medium text.

Do not add a separate display or decorative monospace voice. Tabular numerals support dates and summary values. There is no Workspace eyebrow or heading kicker.

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

### Observed grid and breakpoints

The desktop shell fills `100dvh` with a persistent 248px sidebar and independently scrolling main area. Main content is centered at a maximum width of 1320px with 40px vertical and 36px horizontal padding. The compact summary strip uses columns in a 1 / 1.1 / 1.7 ratio, with vertical dividers rather than separate cards. Bordered coursework panels follow with 20px separation.

At 768–1100px, the sidebar becomes 220px, page padding becomes 32px 24px, and the title becomes 34px. At 767px and below, the sidebar becomes a top navigation region with horizontally scrolling labels; page padding becomes 24px 16px and the title 28px.

Responsive coursework responds to the available main-content container: at 760px and below, the final deadline region spans both summary columns below a horizontal divider. At 720px and below, tables become labelled stacked rows; headers remain accessible and explicit table/row/cell roles are preserved. Keep whole dates visible rather than truncating them.

The dashboard shows the three summary regions, a full-width five-assignment table with `View all upcoming`, then a three-column course roster. Upcoming shares the actual `AssignmentTable` component and displays every upcoming assignment with points metadata. The assignment columns are Course / Assignment / Due Date (20% / 44% / 36%); the course roster is Course / Name / Upcoming (22% / 54% / 24%). These are the current surface expressions, not a template for every future page.

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

Use white coursework panels with subtle one-pixel borders and `rounded.surface` corners. Navigation uses the same compact corner scale; routine buttons and notices use `rounded.control`; disabled `Soon` labels use `rounded.small-label`.

Summary regions are unboxed and separated by rules. Tables use horizontal row borders. Course identity dots remain circular and compact (8px). Do not wrap summary regions or individual assignments in separate floating cards.

The latest user direction requires flat surfaces without gradients or shadows. Avoid extreme pill shapes for normal containers and multiple nested rounded containers.

## Components

### Observed shared patterns

Routine buttons use white fill, a subtle border, 13px medium dark text, and a lightly tinted hover state. Navigation uses consistent authored SVG strokes (20px boxes), lavender active fill with purple text, quiet neutral hover fill, and explicit disabled labels. Reuse the original EasyCanvas logo files in their original purple, coral, and teal colors without a grayscale filter. The brand leads directly into navigation without a Workspace eyebrow.

`AssignmentTable` is a shared presentation component: it receives assignments, an accessible caption, and optional points visibility. Pages keep responsibility for fetching, status messages, and the five-item dashboard preview. `formatCourseCode` supplies the shared readable course label while retaining unfamiliar Canvas formats. Neither changes the source of academic facts.

The Coursework panel is a white bordered container with an 8px radius. Its header uses compact padding, with a pale table-header band below. Rows show separate course, assignment, and due-date fields. The course roster shares the table styling, stable Canvas course IDs, and small course identity dots. Counts and existing disabled View text remain visible. At narrow container widths, labelled rows retain table semantics and full dates. Coverage notices use a subtle border and readable neutral copy. The implemented UI has no input component or generic tag system to extract.

Links and buttons use a 2px purple focus outline offset by 3px; state transitions last 150ms and respect reduced motion. Selection uses pale lavender; the scrolling main area uses a thin neutral scrollbar.

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
- Use a lavender fill and purple text for the active item; keep the other navigation states neutral.
- Use consistent icons.
- Keep labels visible on normal desktop widths.
- Do not use gradients as the default sidebar treatment.
- Keep the seven existing labels. Unimplemented destinations remain visibly disabled with a `Soon` label; do not imply their features exist.
- Avoid oversized branding that wastes vertical space.

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

Recommended content hierarchy:

1. concise page header
2. high-value summary
3. upcoming or priority work
4. course overview

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

The Priorities page should show ranked work and explain why.

Example:

```text
Static Analysis Homework
High Priority

Due tomorrow · Unsubmitted · High grade impact
```

Rules:

- Priority should come from deterministic application logic.
- AI may explain the result but should not silently invent the ranking.
- If a numeric score is shown, provide plain-English reasons.
- Avoid unexplained percentages or confidence-style numbers.

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
