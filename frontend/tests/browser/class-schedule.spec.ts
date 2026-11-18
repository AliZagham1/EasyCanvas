import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

type Meeting = { meeting_id: string; course_id: number; kind: string; days: string[]; start_time: string; end_time: string; location: string | null;
  source: string; source_text: string | null; fingerprint: string | null };
type Suggestion = { fingerprint: string; days: string[]; start_time: string; end_time: string; location: string | null; kind: string; source_text: string; confidence: string };
type Course = { course_id: number; course_code: string; name: string; time_zone: string; meetings: Meeting[]; suggestions: Suggestion[]; hints: { days: string[]; source_text: string }[]; dismissed_count: number };

const FP = 'a1b2c3d4e5f60718';
const suggestion = (over: Partial<Suggestion> = {}): Suggestion => ({ fingerprint: FP, days: ['TUE', 'THU'], start_time: '14:00', end_time: '15:20', location: 'WH 311',
  kind: 'lecture', source_text: 'WH 311 Tu/Th- 2:00 pm to 3:20 pm', confidence: 'high', ...over });
const baseCourses = (): Course[] => [
  { course_id: 1, course_code: '2268-CSE-3314-004', name: 'PROFESSIONAL PRACTICES', time_zone: 'America/Chicago', meetings: [], suggestions: [suggestion()], hints: [], dismissed_count: 0 },
  { course_id: 2, course_code: '2268-CSE-3320-003', name: 'OPERATING SYSTEMS', time_zone: 'America/Chicago', meetings: [], suggestions: [],
    hints: [{ days: ['TUE', 'THU'], source_text: 'Class Meetings: Tuesdays and Thursdays.' }], dismissed_count: 0 },
];

/** A small stateful stand-in for the backend so every click is checked end to end. */
async function mockSchedule(page: Page, courses: Course[] = baseCourses(), options: { failNextCreate?: string } = {}) {
  const state = { courses, posts: [] as unknown[], puts: [] as unknown[], nextId: 1, failNextCreate: options.failNextCreate };
  const view = () => ({ courses: state.courses });
  await page.route('**/api/schedule', (route) => route.fulfill({ json: view() }));
  await page.route('**/api/schedule/meetings', (route) => {
    const body = route.request().postDataJSON();
    state.posts.push(body);
    if (state.failNextCreate) { const detail = state.failNextCreate; state.failNextCreate = undefined; return route.fulfill({ status: 422, json: { detail } }); }
    const course = state.courses.find((c) => c.course_id === body.course_id)!;
    const meeting: Meeting = { meeting_id: String(state.nextId++).padStart(32, '0'), course_id: body.course_id, kind: body.kind ?? 'lecture', days: body.days,
      start_time: body.start_time, end_time: body.end_time, location: body.location ?? null, source: body.source ?? 'manual', source_text: body.source_text ?? null, fingerprint: body.fingerprint ?? null };
    course.meetings.push(meeting);
    course.suggestions = course.suggestions.filter((s) => s.fingerprint !== body.fingerprint);
    return route.fulfill({ status: 201, json: meeting });
  });
  await page.route('**/api/schedule/meetings/*', (route) => {
    const id = route.request().url().split('/').pop()!;
    const course = state.courses.find((c) => c.meetings.some((m) => m.meeting_id === id))!;
    if (route.request().method() === 'DELETE') { course.meetings = course.meetings.filter((m) => m.meeting_id !== id); return route.fulfill({ json: { ok: true } }); }
    const body = route.request().postDataJSON(); state.puts.push(body);
    Object.assign(course.meetings.find((m) => m.meeting_id === id)!, body);
    return route.fulfill({ json: course.meetings.find((m) => m.meeting_id === id) });
  });
  await page.route('**/api/schedule/suggestions/dismiss', (route) => {
    const body = route.request().postDataJSON();
    const course = state.courses.find((c) => c.course_id === body.course_id)!;
    course.suggestions = course.suggestions.filter((s) => s.fingerprint !== body.fingerprint); course.dismissed_count += 1;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/schedule/courses/*/dismissed', (route) => {
    const id = Number(route.request().url().split('/').slice(-2)[0]);
    const course = state.courses.find((c) => c.course_id === id)!; course.dismissed_count = 0; course.suggestions = [suggestion()];
    return route.fulfill({ json: { ok: true } });
  });
  return state;
}

// Tuesday 2026-11-17 12:00 in Chicago.
const NOW = new Date('2026-11-17T18:00:00Z');

test('a suggestion shows its quoted syllabus line and is not used until confirmed', async ({ page }) => {
  await mockSchedule(page); await page.clock.setFixedTime(NOW); await page.goto('/class-schedule');
  await expect(page.getByRole('heading', { name: 'Class Schedule' })).toBeVisible();
  await expect(page.getByText('1 class time found in your syllabi.')).toBeVisible();
  await expect(page.getByText('“WH 311 Tu/Th- 2:00 pm to 3:20 pm”')).toBeVisible();
  await expect(page.getByText('Tue, Thu · 2:00 PM – 3:20 PM · WH 311')).toBeVisible();
  await expect(page.getByText('No confirmed classes yet')).toBeVisible();
  await expect(page.locator('.schedule-block')).toHaveCount(0);                  // nothing trusted yet
  await expect(page.getByLabel('Next class')).toHaveCount(0);                    // no confirmed class yet, so no next-class card here
});

test('confirming fills the week, sets the next class and records where it came from', async ({ page }) => {
  const state = await mockSchedule(page); await page.clock.setFixedTime(NOW); await page.goto('/class-schedule');
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(page.getByText('Class time confirmed.')).toBeVisible();
  await expect(page.getByText('Confirmed from your syllabus')).toBeVisible();
  await expect(page.locator('.schedule-block')).toHaveCount(2);                   // Tue and Thu
  await expect(page.getByText('Found in your syllabus')).toHaveCount(0);
  expect(state.posts[0]).toMatchObject({ course_id: 1, days: ['TUE', 'THU'], start_time: '14:00', end_time: '15:20', source: 'syllabus', fingerprint: FP });
  const next = page.getByLabel('Next class');
  await expect(next).toContainText('CSE 3314');
  await expect(next).toContainText('Starts in 2 h');                              // 12:00 Tuesday in Chicago -> 14:00
});

test('editing a suggestion before confirming saves the corrected time', async ({ page }) => {
  const state = await mockSchedule(page); await page.goto('/class-schedule');
  await page.getByRole('button', { name: 'Edit first' }).click();
  await page.getByLabel('Start').fill('14:10'); await page.getByLabel('Location').fill('NH 110');
  await page.getByRole('button', { name: 'Confirm', exact: true }).last().click();
  await expect(page.getByText('Class time confirmed.')).toBeVisible();
  expect(state.posts[0]).toMatchObject({ start_time: '14:10', location: 'NH 110', fingerprint: FP });
  await expect(page.getByText('Tue, Thu · 2:10 PM – 3:20 PM · NH 110')).toBeVisible();
});

test('dismissing a wrong suggestion hides it and it can be restored', async ({ page }) => {
  await mockSchedule(page); await page.goto('/class-schedule');
  await page.getByRole('button', { name: 'Not right' }).click();
  await expect(page.getByText('Suggestion dismissed.')).toBeVisible();
  await expect(page.getByText('“WH 311 Tu/Th- 2:00 pm to 3:20 pm”')).toHaveCount(0);
  await page.getByRole('button', { name: 'Restore 1 dismissed suggestion' }).click();
  await expect(page.getByText('“WH 311 Tu/Th- 2:00 pm to 3:20 pm”')).toBeVisible();
});

test('a days-only hint opens the form with those days selected and no invented time', async ({ page }) => {
  const state = await mockSchedule(page); await page.goto('/class-schedule');
  await expect(page.getByText('Your syllabus mentions days but no time')).toBeVisible();
  await page.getByRole('button', { name: 'Add the time' }).click();
  await expect(page.getByLabel('Tuesday')).toBeChecked(); await expect(page.getByLabel('Thursday')).toBeChecked();
  await expect(page.getByLabel('Monday')).not.toBeChecked();
  await expect(page.getByLabel('Start')).toHaveValue(''); await expect(page.getByLabel('End')).toHaveValue('');
  await page.getByLabel('Start').fill('12:30'); await page.getByLabel('End').fill('13:45');
  await page.getByRole('form', { name: 'Add class time' }).getByRole('button', { name: 'Add class time' }).click();
  await expect(page.getByText('Class time added.')).toBeVisible();
  expect(state.posts[0]).toMatchObject({ course_id: 2, days: ['TUE', 'THU'], start_time: '12:30', end_time: '13:45' });
  expect((state.posts[0] as { source?: string }).source).toBeUndefined();         // typed by the student: no syllabus origin
});

test('manual entry checks the obvious mistakes and shows backend errors', async ({ page }) => {
  const state = await mockSchedule(page, baseCourses(), { failNextCreate: 'That time overlaps something else.' });
  await page.goto('/class-schedule');
  await page.getByRole('button', { name: 'Add class time' }).first().click();
  const submit = page.getByRole('form', { name: 'Add class time' }).getByRole('button', { name: 'Add class time' });
  await submit.click();
  await expect(page.getByRole('alert').filter({ hasText: 'Choose at least one day.' })).toBeVisible();
  await page.getByLabel('Wednesday').check();
  await page.getByLabel('Start').fill('10:00'); await page.getByLabel('End').fill('09:00');
  await submit.click();
  await expect(page.getByRole('alert').filter({ hasText: 'The end time must be after the start time.' })).toBeVisible();
  expect(state.posts).toHaveLength(0);                                            // never sent
  await page.getByLabel('End').fill('11:00');
  await submit.click();
  await expect(page.getByRole('alert').filter({ hasText: 'That time overlaps something else.' })).toBeVisible();   // backend message
  await submit.click();
  await expect(page.getByText('Class time added.')).toBeVisible();
  await expect(page.locator('.schedule-block')).toHaveCount(1);
});

test('edit and delete work, and delete asks first', async ({ page }) => {
  const meeting: Meeting = { meeting_id: '0'.repeat(31) + '9', course_id: 1, kind: 'lab', days: ['WED'], start_time: '17:30', end_time: '19:00', location: 'ERB 100', source: 'manual', source_text: null, fingerprint: null };
  const courses = baseCourses(); courses[0].suggestions = []; courses[0].meetings = [meeting];
  const state = await mockSchedule(page, courses); await page.goto('/class-schedule');
  await expect(page.getByText('Wed · 5:30 PM – 7:00 PM · ERB 100')).toBeVisible();
  await expect(page.getByText('Lab · Added by you')).toBeVisible();
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('End').fill('19:30');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText('Wed · 5:30 PM – 7:30 PM · ERB 100')).toBeVisible();
  expect(state.puts[0]).toMatchObject({ end_time: '19:30' });
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByText('Delete this class time?')).toBeVisible();
  await page.getByRole('button', { name: 'Keep it' }).click();
  await expect(page.getByText('Wed · 5:30 PM')).toBeVisible();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByRole('button', { name: 'Yes, delete' }).click();
  await expect(page.getByText('Class time deleted.')).toBeVisible();
  await expect(page.getByText('Wed · 5:30 PM')).toHaveCount(0);
});

test('empty and error states are distinct', async ({ page }) => {
  await mockSchedule(page, []); await page.goto('/class-schedule');
  await expect(page.getByText('No courses are saved yet.')).toBeVisible();
  const failing = await page.context().newPage();
  await failing.route('**/api/schedule', (route) => route.fulfill({ status: 503, json: { detail: 'down' } }));
  await failing.goto('/class-schedule');
  await expect(failing.getByRole('alert').filter({ hasText: 'Unable to load the class schedule.' })).toBeVisible();
  await expect(failing.getByText('No courses are saved yet.')).toHaveCount(0);
  let healthy = false;
  await failing.unroute('**/api/schedule');
  await failing.route('**/api/schedule', (route) => { healthy = true; return route.fulfill({ json: { courses: baseCourses() } }); });
  await failing.getByRole('button', { name: 'Try again' }).click();
  await expect(failing.getByText('Found in your syllabus')).toBeVisible(); expect(healthy).toBe(true);
});

test('desktop shows the weekly grid; sidebar link is live', async ({ page }) => {
  const courses = baseCourses(); courses[0].suggestions = [];
  courses[0].meetings = [{ meeting_id: '1'.padStart(32, '0'), course_id: 1, kind: 'lecture', days: ['TUE', 'THU'], start_time: '14:00', end_time: '15:20', location: 'WH 311', source: 'manual', source_text: null, fingerprint: null }];
  await mockSchedule(page, courses); await page.setViewportSize({ width: 1280, height: 900 }); await page.goto('/class-schedule');
  await expect(page.locator('.schedule-grid')).toBeVisible();
  await expect(page.locator('.schedule-dayhead')).toHaveText(['Mon', 'Tue', 'Wed', 'Thu', 'Fri']);
  await expect(page.locator('.schedule-block')).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Class Schedule' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('Soon')).toHaveCount(0);                            // no sidebar link is left as "Soon"
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
});

test('phones get a day-by-day agenda instead of the grid, with no sideways scrolling', async ({ page }) => {
  const courses = baseCourses(); courses[0].suggestions = [];
  courses[0].meetings = [{ meeting_id: '1'.padStart(32, '0'), course_id: 1, kind: 'lecture', days: ['TUE', 'THU'], start_time: '14:00', end_time: '15:20', location: 'WH 311', source: 'manual', source_text: null, fingerprint: null }];
  await mockSchedule(page, courses); await page.setViewportSize({ width: 390, height: 900 }); await page.goto('/class-schedule');
  await expect(page.locator('.schedule-grid')).toBeHidden();
  await expect(page.locator('.schedule-agenda')).toBeVisible();
  await expect(page.locator('.schedule-agenda h3')).toHaveText(['Tuesday', 'Thursday']);
  await expect(page.locator('.schedule-agenda li').first()).toContainText('2:00 PM – 3:20 PM');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
});

test('the Dashboard shows the next class from confirmed meetings only', async ({ page }) => {
  const courses = baseCourses(); courses[0].suggestions = [];
  courses[0].meetings = [{ meeting_id: '1'.padStart(32, '0'), course_id: 1, kind: 'lecture', days: ['TUE'], start_time: '14:00', end_time: '15:20', location: 'WH 311', source: 'manual', source_text: null, fingerprint: null }];
  await mockSchedule(page, courses);
  await page.route('**/api/courses', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/ingestion', (route) => route.fulfill({ json: { status: 'completed' } }));
  await page.clock.setFixedTime(NOW); await page.goto('/');
  const next = page.getByLabel('Next class');
  await expect(next).toContainText('Next class'); await expect(next).toContainText('CSE 3314'); await expect(next).toContainText('Starts in 2 h');
  await next.getByRole('link', { name: 'View schedule' }).click();
  await expect(page).toHaveURL(/class-schedule/);
});
