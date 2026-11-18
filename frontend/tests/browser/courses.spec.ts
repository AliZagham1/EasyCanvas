import { test, expect } from '@playwright/test';
import { mockConsent } from './consent';
import type { Page } from '@playwright/test';

test.beforeEach(async ({ page }) => { await mockConsent(page); });

const NOW = new Date('2026-11-17T18:00:00Z'); // Tuesday 12:00 in Chicago
const at = (hours: number) => new Date(NOW.getTime() + hours * 3_600_000).toISOString();
const counts = { assignments: 3, modules: 2, files: 5, pages: 1, quizzes: 0, announcements: 6 };
const meeting = { meeting_id: '1'.padStart(32, '0'), course_id: 1, kind: 'lecture', days: ['TUE'], start_time: '14:00', end_time: '15:20', location: 'WH 311',
  source: 'manual', source_text: null, fingerprint: null };
const catalog = [
  { course_id: 1, course_code: '2268-CSE-4321-002', name: '2268-CSE-4321-002-SOFTWARE TESTING & MAINTENANCE', time_zone: 'America/Chicago', canvas_url: 'https://canvas.example/courses/1',
    synced_at: '2026-11-17T20:00:00Z', counts, meetings: [meeting], latest_announcement: { title: 'Hello', posted_at: at(-10) } },
  { course_id: 2, course_code: '2268-CSE-3320-003', name: '2268-CSE-3320-003-OPERATING SYSTEMS', time_zone: 'America/Chicago', canvas_url: 'https://canvas.example/courses/2',
    synced_at: null, counts: { ...counts, announcements: 0 }, meetings: [], latest_announcement: null },
];
const details = (id: number) => ({ ...catalog[id - 1], latest_announcement: undefined, syllabus_url: `https://canvas.example/courses/${id}/assignments/syllabus`,
  announcements: Array.from({ length: 7 }, (_, i) => ({ title: `News ${i + 1}`, url: `https://canvas.example/courses/${id}/discussion_topics/${i}`, posted_at: at(-i - 1), excerpt: i === 0 ? 'Plain text excerpt' : '' })),
  modules: [{ module_id: 10, name: 'Week 1', items: [{ title: 'Slides A', type: 'File', url: 'https://canvas.example/courses/1/modules/items/11' }, { title: 'Reading', type: 'Page', url: null }] },
            { module_id: 11, name: 'Week 2', items: [] }] });
let n = 0;
const assignment = (course: number, over: Record<string, unknown> = {}) => ({ id: ++n, course_id: course, course_code: `2268-CSE-${course === 1 ? '4321' : '3320'}-00${course}`, course_name: 'x',
  name: `Task ${n}`, html_url: `https://canvas.example/courses/${course}/assignments/${n}`, due_at: at(48), lock_at: null, unlock_at: null, locked_for_user: false, points_possible: 10,
  submission_types: ['online_upload'], is_group: false, submission_status: 'incomplete', submitted_at: null, missing: false, late: false, ...over });
const assignments = () => [
  assignment(1, { name: 'Overdue lab', due_at: at(-30) }), assignment(1, { name: 'Soon quiz', due_at: at(6) }), assignment(1, { name: 'Later project', due_at: at(200) }),
  assignment(1, { name: 'Finished hw', submission_status: 'graded' }), assignment(1, { name: 'Undated', due_at: null }),
  assignment(2, { name: 'Other course task', due_at: at(10) }),
];
const search = [{ course_id: 1, title: 'CSE 4321', eligible_chunks: 10, indexed_chunks: 10, pending_chunks: 0, failed_chunks: 0, remaining_chunks: 0, status: 'ready', automatic: true, last_successful_at: null, documents: [] },
  { course_id: 2, title: 'CSE 3320', eligible_chunks: 10, indexed_chunks: 4, pending_chunks: 6, failed_chunks: 0, remaining_chunks: 6, status: 'pending', automatic: true, last_successful_at: null, documents: [] }];

async function mock(page: Page, options: { priorities?: 'ok' | 'fail'; catalog?: 'ok' | 'fail' | 'empty'; detailStatus?: number } = {}) {
  await page.route('**/api/course-catalog', (route) => options.catalog === 'fail' ? route.fulfill({ status: 503, json: { detail: 'down' } })
    : route.fulfill({ json: { courses: options.catalog === 'empty' ? [] : catalog } }));
  await page.route('**/api/courses/*/details', (route) => {
    const id = Number(new URL(route.request().url()).pathname.split('/')[3]);
    if (options.detailStatus) return route.fulfill({ status: options.detailStatus, json: { detail: 'x' } });
    return id === 1 || id === 2 ? route.fulfill({ json: details(id) }) : route.fulfill({ status: 404, json: { detail: 'Course not found.' } });
  });
  await page.route('**/api/priorities**', (route) => options.priorities === 'fail' ? route.fulfill({ status: 502, json: { detail: 'x' } })
    : route.fulfill({ json: { fetched_at: at(0), cached: false, courses: [{ id: 1, course_code: 'a', name: 'a' }, { id: 2, course_code: 'b', name: 'b' }], assignments: assignments(), failed_course_ids: [] } }));
  await page.route('**/api/search/status', (route) => route.fulfill({ json: { courses: search } }));
}

test('the Courses page shows one card per course with next class, workload and readiness', async ({ page }) => {
  await mock(page); await page.clock.setFixedTime(NOW); await page.goto('/courses');
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  const cards = page.locator('.course-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText('CSE 3320');                           // sorted by course code
  await expect(cards.nth(0)).toContainText('OPERATING SYSTEMS');                  // Canvas prefix stripped
  await expect(cards.nth(0)).toContainText('No class times yet');
  await expect(cards.nth(0)).toContainText('1 due within 24 hours');
  await expect(cards.nth(0)).toContainText('6 pending'.replace('6 pending', 'passages pending'));
  await expect(cards.nth(1)).toContainText('CSE 4321');
  await expect(cards.nth(1)).toContainText('Next class · Tue 2:00 PM – 3:20 PM · WH 311');
  await expect(cards.nth(1)).toContainText('Starts in 2 h');
  await expect(cards.nth(1)).toContainText('1 overdue · 1 due in the next 3 days');
  await expect(cards.nth(1)).toContainText('2 modules · 5 files · 6 announcements');
  await expect(cards.nth(1)).toContainText('Study Assistant ready');
  await expect(cards.nth(1)).toHaveAttribute('href', '/courses/1');
});

test('the sidebar link is live and stays highlighted on a course page', async ({ page }) => {
  await mock(page); await page.goto('/courses');
  await expect(page.getByRole('link', { name: 'Courses', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.locator('.course-card').first().click();
  await expect(page).toHaveURL(/\/courses\/2$/);
  await expect(page.getByRole('link', { name: 'Courses', exact: true })).toHaveClass(/nav-link-active/);
  await expect(page.getByText('Soon')).toHaveCount(0);                            // nothing is left as "Soon"
});

test('a course page shows its own assignments with the Priorities labels, and nothing from other courses', async ({ page }) => {
  await mock(page); await page.clock.setFixedTime(NOW); await page.goto('/courses/1');
  await expect(page.getByRole('heading', { name: 'CSE 4321', level: 1 })).toBeVisible();
  await expect(page.getByText('SOFTWARE TESTING & MAINTENANCE')).toBeVisible();
  await expect(page.locator('.priority-stat').nth(0)).toContainText('1');          // overdue
  const section = page.getByRole('region', { name: 'Assignments' });
  await expect(section.getByRole('heading', { name: /Overdue/ })).toBeVisible();
  await expect(section.getByText('Overdue lab')).toBeVisible();
  await expect(section.getByText('Soon quiz')).toBeVisible();
  await expect(section.getByText('Later project')).toBeVisible();
  await expect(section.getByRole('heading', { name: /No due date/ })).toBeVisible();
  await expect(section.getByText('Other course task')).toHaveCount(0);
  await expect(section.getByText('Finished hw')).toHaveCount(0);                  // completed is hidden until asked for
  await expect(section.locator('.priority-meta').first()).not.toContainText('CSE 4321');   // course name is not repeated on its own page
  await expect(section.getByRole('link', { name: /Soon quiz/ })).toHaveAttribute('href', 'https://canvas.example/courses/1/assignments/2');
  await section.getByLabel(/Show completed and closed/).check();
  await expect(section.getByText('Finished hw')).toBeVisible();
});

test('the course page links to Canvas, the syllabus and the Study Assistant for this course', async ({ page }) => {
  await mock(page); await page.goto('/courses/1');
  await expect(page.getByRole('link', { name: /Ask the Study Assistant/ })).toHaveAttribute('href', '/study-assistant?course=1');
  await expect(page.getByRole('link', { name: /Open in Canvas/ })).toHaveAttribute('href', 'https://canvas.example/courses/1');
  await expect(page.getByRole('link', { name: /Syllabus/ })).toHaveAttribute('href', 'https://canvas.example/courses/1/assignments/syllabus');
  await expect(page.getByRole('link', { name: /Open in Canvas/ })).toHaveAttribute('target', '_blank');
  await expect(page.getByRole('link', { name: /Open in Canvas/ })).toHaveAttribute('rel', /noopener/);
  await expect(page.getByText(/Last synced/)).toBeVisible();
});

test('class times, announcements and materials come from saved data', async ({ page }) => {
  await mock(page); await page.clock.setFixedTime(NOW); await page.goto('/courses/1');
  await expect(page.getByLabel('Next class')).toContainText('Starts in 2 h');
  const times = page.getByRole('region', { name: 'Class times' });
  await expect(times).toContainText('Tue · 2:00 PM – 3:20 PM · WH 311');
  await expect(times.getByRole('link', { name: 'Manage in Class Schedule' })).toHaveAttribute('href', '/class-schedule');
  const news = page.getByRole('region', { name: /Announcements/ });
  await expect(news.locator('.announcement-list li')).toHaveCount(5);              // preview of 5 of 7
  await expect(news.getByText('Plain text excerpt')).toBeVisible();
  await news.getByRole('button', { name: 'Show all 7' }).click();
  await expect(news.locator('.announcement-list li')).toHaveCount(7);
  const materials = page.getByRole('region', { name: 'Materials' });
  await expect(materials).toContainText('2 modules · 5 files · 6 announcements');
  await materials.getByText('Week 1').click();
  await expect(materials.getByRole('link', { name: /Slides A/ })).toHaveAttribute('href', 'https://canvas.example/courses/1/modules/items/11');
  await expect(materials.getByText('Reading')).toBeVisible();                      // an item without a link is plain text, not a dead link
  await materials.getByText('Week 2').click();
  await expect(materials.getByText('No files or pages saved for this module.')).toBeVisible();
});

test('a course without class times points to the schedule instead of inventing one', async ({ page }) => {
  await mock(page); await page.goto('/courses/2');
  await expect(page.getByRole('region', { name: 'Class times' })).toContainText('No class times yet.');
  await expect(page.getByLabel('Next class')).toHaveCount(0);
  await expect(page.getByRole('region', { name: /Announcements/ })).toBeVisible();
});

test('if assignment status fails the course page still loads and says so, with no fake zeros', async ({ page }) => {
  await mock(page, { priorities: 'fail' }); await page.goto('/courses/1');
  await expect(page.getByRole('heading', { name: 'CSE 4321', level: 1 })).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: 'Assignment status is unavailable' })).toBeVisible();
  await expect(page.locator('.priority-stat dd')).toHaveText(['Unavailable', 'Unavailable', 'Unavailable', 'Unavailable']);
  await expect(page.getByRole('region', { name: /Announcements/ })).toBeVisible();
  await page.goto('/courses');
  await expect(page.locator('.course-card').first()).toContainText('Assignment status unavailable');
});

test('a missing course and a backend failure are different messages', async ({ page }) => {
  await mock(page); await page.goto('/courses/999');
  await expect(page.getByRole('alert').filter({ hasText: 'not found in your saved courses' })).toBeVisible();
  await page.goto('/courses/abc');
  await expect(page.getByRole('alert').filter({ hasText: 'not found in your saved courses' })).toBeVisible();
  const broken = await page.context().newPage(); await mock(broken, { detailStatus: 503 }); await broken.goto('/courses/1');
  await expect(broken.getByRole('alert').filter({ hasText: 'Unable to load this course' })).toBeVisible();
  await expect(broken.getByText('not found in your saved courses')).toHaveCount(0);
});

test('Courses page empty and error states are distinct', async ({ page }) => {
  await mock(page, { catalog: 'empty' }); await page.goto('/courses');
  await expect(page.getByText('No courses are saved yet.')).toBeVisible();
  const broken = await page.context().newPage(); await mock(broken, { catalog: 'fail' }); await broken.goto('/courses');
  await expect(broken.getByRole('alert').filter({ hasText: 'Unable to load your courses' })).toBeVisible();
  await expect(broken.getByText('No courses are saved yet.')).toHaveCount(0);
});

test('phones: cards and the course page fit without sideways scrolling', async ({ page }) => {
  await mock(page); await page.setViewportSize({ width: 390, height: 900 }); await page.goto('/courses');
  await expect(page.locator('.course-card')).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  await page.goto('/courses/1'); await expect(page.getByRole('heading', { name: 'CSE 4321', level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
});

test('the Dashboard "View" link opens the course page', async ({ page }) => {
  await mock(page);
  await page.route('**/api/courses', (route) => route.fulfill({ json: [{ id: 1, course_code: '2268-CSE-4321-002', name: 'Testing' }] }));
  await page.route('**/api/courses/1/upcoming', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/ingestion', (route) => route.fulfill({ json: { status: 'completed' } }));
  await page.route('**/api/schedule', (route) => route.fulfill({ json: { courses: [] } }));
  await page.goto('/');
  const view = page.getByRole('link', { name: /CSE 4321/ });
  await expect(view).toHaveAttribute('href', '/courses/1');
});

async function assistantMocks(page: Page) {
  await page.route('**/api/courses/*/conversations**', (route) => {
    const course = Number(new URL(route.request().url()).pathname.split('/')[3]);
    const thread = { conversation_id: '00000000-0000-4000-8000-000000000001', course_id: course, title: 'New conversation', messages: [] };
    return route.request().method() === 'POST' ? route.fulfill({ status: 201, json: thread }) : route.fulfill({ json: route.request().url().includes('00000000-0000') ? thread : [] });
  });
  await page.route('http://127.0.0.1:8000/api/courses', (route) => route.fulfill({ json: [{ id: 42, course_code: 'CSE 3311', name: 'Software Engineering' }, { id: 43, course_code: 'CSE 3320', name: 'Operating Systems' }] }));
  await page.route('**/api/courses/*/search-index', (route) => route.fulfill({ json: { ...search[0], course_id: 43 } }));
}

test('"Ask the Study Assistant" opens the assistant with that course already chosen', async ({ page }) => {
  await assistantMocks(page); await page.goto('/study-assistant?course=43');
  await expect(page.locator('#assistant-course')).toHaveValue('43');
  await page.locator('#assistant-course').selectOption('42');                       // choosing another course afterwards is respected
  await expect(page.locator('#assistant-course')).toHaveValue('42');
});

test('the Study Assistant ignores a course that is not one of yours, and works without the parameter', async ({ page }) => {
  await assistantMocks(page); await page.goto('/study-assistant?course=999');
  await expect(page.locator('#assistant-course')).toHaveValue('');
  await page.goto('/study-assistant?course=abc'); await expect(page.locator('#assistant-course')).toHaveValue('');
  await page.goto('/study-assistant'); await expect(page.locator('#assistant-course')).toHaveValue('');
});

test('modules without ids never produce duplicate React keys, and every module keeps its own items', async ({ page }) => {
  await mock(page);
  await page.route('**/api/courses/1/details', (route) => route.fulfill({ json: { ...details(1), modules: [
    { module_id: null, name: 'Week A', items: [{ title: 'A item', type: 'File', url: null }] },
    { module_id: null, name: 'Week B', items: [{ title: 'B item', type: 'Page', url: null }] }] } }));
  const problems: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error' && /same key|unique "key"/i.test(message.text())) problems.push(message.text()); });
  await page.goto('/courses/1');
  const materials = page.getByRole('region', { name: 'Materials' });
  await expect(materials.locator('.module')).toHaveCount(2);
  await materials.getByText('Week A').click(); await materials.getByText('Week B').click();
  await expect(materials.getByText('A item')).toBeVisible(); await expect(materials.getByText('B item')).toBeVisible();
  expect(problems).toEqual([]);
});
