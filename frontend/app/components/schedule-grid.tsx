import { getCourseAccent } from "@/lib/coursework";
import { DAY_LONG, DAY_SHORT, DAYS, KIND_LABEL, courseLabel, courseTitle, formatRange, formatTime, layoutWeek, toMinutes } from "@/lib/schedule";
import type { DayCode, Meeting, ScheduleCourse } from "@/lib/schedule";

const HOUR_PX = 52;

/** Weekly view of confirmed meetings: a grid on wide screens, an agenda list on phones (and for screen readers). */
export default function ScheduleGrid({ courses }: { courses: ScheduleCourse[] }) {
  const byCourse = new Map(courses.map((course) => [course.course_id, course]));
  const meetings: Meeting[] = courses.flatMap((course) => course.meetings);
  const { days, startHour, endHour, blocks } = layoutWeek(meetings);
  const hours = Array.from({ length: endHour - startHour }, (_, index) => startHour + index);
  const label = (meeting: Meeting) => courseLabel(byCourse.get(meeting.course_id) ?? { course_code: null, name: null });
  const agendaDays: DayCode[] = DAYS.filter((day) => meetings.some((meeting) => meeting.days.includes(day)));

  return (
    <div className="schedule-view">
      <div className="schedule-grid" aria-hidden="true" style={{ "--days": days.length, "--hour": `${HOUR_PX}px` } as React.CSSProperties}>
        <div className="schedule-corner" />
        {days.map((day) => <div key={day} className="schedule-dayhead">{DAY_SHORT[day]}</div>)}
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
                <div key={`${day}-${block.meeting.meeting_id}`} className={`schedule-block ${getCourseAccent(block.meeting.course_id)}`}
                  title={`${label(block.meeting)} · ${formatRange(block.meeting.start_time, block.meeting.end_time)}${block.meeting.location ? ` · ${block.meeting.location}` : ""}`}
                  style={{ top, height, left: `${(block.column / block.columns) * 100}%`, width: `${100 / block.columns}%` }}>
                  <strong>{label(block.meeting)}</strong>
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
                  <li key={meeting.meeting_id} className={getCourseAccent(meeting.course_id)}>
                    <span className="agenda-time">{formatRange(meeting.start_time, meeting.end_time)}</span>
                    <span><strong>{label(meeting)}</strong>{meeting.kind !== "lecture" ? ` · ${KIND_LABEL[meeting.kind]}` : ""}{meeting.location ? ` · ${meeting.location}` : ""}</span>
                  </li>
                ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
