import Link from "next/link";
import { getCourseAccent } from "@/lib/coursework";
import { isStudy, DAY_LONG, DAY_SHORT, DAYS, KIND_LABEL, courseLabel, courseTitle, formatRange, formatTime, layoutWeek, toMinutes } from "@/lib/schedule";
import type { DayCode, Meeting, ScheduleCourse } from "@/lib/schedule";

const HOUR_PX = 52;
const MAX_CHIPS = 3;

/** An unsubmitted, not-yet-due assignment placed on a day of the week being shown. */
export type GridDeadline = {
  key: string; courseId: number; course: string; title: string; url: string | null; dueAt: string; dayIndex: number;
  /** "exam" is a date the student entered in Study Plans: it has no Canvas page, so it links to the plan instead. */
  kind?: "assignment" | "exam";
};

const timeOf = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/**
 * Weekly view: confirmed meetings plus unsubmitted deadlines. A grid on wide screens, an agenda list on phones (and for screen readers).
 * Deadlines sit in a "Due" row under the day names (their times can be outside class hours); the time grid below is decorative.
 */
export default function ScheduleGrid({ courses, deadlines = [] }: { courses: ScheduleCourse[]; deadlines?: GridDeadline[] }) {
  const byCourse = new Map(courses.map((course) => [course.course_id, course]));
  const meetings: Meeting[] = courses.flatMap((course) => course.meetings);
  const layout = layoutWeek(meetings);
  const { startHour, endHour, blocks } = layout;
  const dueDays = new Set<DayCode>(deadlines.map((deadline) => DAYS[deadline.dayIndex]));
  const days = DAYS.filter((day) => layout.days.includes(day) || dueDays.has(day));
  const hours = Array.from({ length: endHour - startHour }, (_, index) => startHour + index);
  const label = (meeting: Meeting) => courseLabel(byCourse.get(meeting.course_id) ?? { course_code: null, name: null });
  const agendaDays: DayCode[] = DAYS.filter((day) => dueDays.has(day) || meetings.some((meeting) => meeting.days.includes(day)));
  const dueOn = (day: DayCode) => deadlines.filter((deadline) => DAYS[deadline.dayIndex] === day).sort((a, b) => Date.parse(a.dueAt) - Date.parse(b.dueAt));
  const template = { "--days": days.length, "--hour": `${HOUR_PX}px` } as React.CSSProperties;

  return (
    <div className="schedule-view">
      {meetings.some(isStudy) && <p className="schedule-legend"><span className="legend-swatch" /> Study block</p>}
      <div className="schedule-head" style={template}>
        <div className="schedule-corner" aria-hidden="true" />
        {days.map((day) => <div key={day} className="schedule-dayhead" aria-hidden="true">{DAY_SHORT[day]}</div>)}
        {deadlines.length > 0 && <>
          <div className="schedule-due-label">Due</div>
          {days.map((day) => {
            const items = dueOn(day);
            return (
              <div key={day} className="schedule-due-cell">
                {items.slice(0, MAX_CHIPS).map((item) => {
                  const exam = item.kind === "exam";
                  const name = `${item.title}, ${exam ? "exam, " : ""}${item.course}, ${exam ? "on" : "due"} ${DAY_LONG[day]} at ${timeOf(item.dueAt)}`;
                  const inner = <><span className={`course-dot ${getCourseAccent(item.courseId)}`} aria-hidden="true" /><span className="due-time">{timeOf(item.dueAt)}</span><span className="due-title">{item.title}</span>{exam && <span className="due-tag" aria-hidden="true">Exam</span>}</>;
                  if (exam) return <Link key={item.key} className="schedule-due is-exam" href="/study-plans" title={name} aria-label={`${name} (opens Study Plans)`}>{inner}</Link>;
                  return item.url
                    ? <a key={item.key} className="schedule-due" href={item.url} target="_blank" rel="noopener noreferrer" title={name} aria-label={`${name} (opens in Canvas)`}>{inner}</a>
                    : <span key={item.key} className="schedule-due" title={name} aria-label={name}>{inner}</span>;
                })}
                {items.length > MAX_CHIPS && <Link href="/upcoming" className="schedule-due-more">+{items.length - MAX_CHIPS} more</Link>}
              </div>
            );
          })}
        </>}
      </div>
      <div className="schedule-grid" aria-hidden="true" style={template}>
        <div className="schedule-times" style={{ height: hours.length * HOUR_PX }}>
          {hours.map((hour) => <span key={hour} style={{ top: (hour - startHour) * HOUR_PX }}>{formatTime(`${String(hour).padStart(2, "0")}:00`)}</span>)}
        </div>
        {days.map((day) => (
          <div key={day} className="schedule-daycol" style={{ height: hours.length * HOUR_PX }}>
            {blocks.filter((block) => block.day === day).map((block) => {
              const top = ((block.startMin - startHour * 60) / 60) * HOUR_PX;
              const height = Math.max(((block.endMin - block.startMin) / 60) * HOUR_PX, 22);
              const course = byCourse.get(block.meeting.course_id);
              return (
                <div key={`${day}-${block.meeting.meeting_id}`} className={`schedule-block ${getCourseAccent(block.meeting.course_id)}${isStudy(block.meeting) ? " is-study" : ""}`}
                  title={`${label(block.meeting)} · ${formatRange(block.meeting.start_time, block.meeting.end_time)}${block.meeting.location ? ` · ${block.meeting.location}` : ""}`}
                  style={{ top, height, left: `${(block.column / block.columns) * 100}%`, width: `${100 / block.columns}%` }}>
                  <strong>{isStudy(block.meeting) ? "Study · " : ""}{label(block.meeting)}</strong>
                  <span>{formatTime(block.meeting.start_time)}</span>
                  {block.meeting.location && height >= 52 && <span>{block.meeting.location}</span>}
                  {course?.name && height >= 80 && <span className="schedule-block-name">{courseTitle(course.name)}</span>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="schedule-agenda" role="list" aria-label="Weekly class schedule">
        {agendaDays.map((day) => (
          <section key={day} role="listitem" aria-label={DAY_LONG[day]}>
            <h3>{DAY_LONG[day]}</h3>
            <ul>
              {meetings.filter((meeting) => meeting.days.includes(day)).sort((a, b) => toMinutes(a.start_time) - toMinutes(b.start_time))
                .map((meeting) => (
                  <li key={meeting.meeting_id} className={`${getCourseAccent(meeting.course_id)}${isStudy(meeting) ? " is-study" : ""}`}>
                    <span className="agenda-time">{formatRange(meeting.start_time, meeting.end_time)}</span>
                    <span><strong>{label(meeting)}</strong>{meeting.kind !== "lecture" ? ` · ${KIND_LABEL[meeting.kind]}` : ""}{meeting.location ? ` · ${meeting.location}` : ""}</span>
                  </li>
                ))}
              {/* Shown on phones only; on wide screens the Due row above already carries these, so they are not read twice. */}
              {dueOn(day).map((item) => (
                <li key={item.key} className={`agenda-due ${getCourseAccent(item.courseId)}`}>
                  <span className="agenda-time">{item.kind === "exam" ? "Exam" : "Due"} {timeOf(item.dueAt)}</span>
                  <span><strong>{item.title}</strong> · {item.course}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
