"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { DAY_SHORT, courseLabel, describeNext, formatRange, getSchedule, nextClass } from "@/lib/schedule";
import type { ScheduleCourse } from "@/lib/schedule";

const TICK_MS = 30_000;

/** "Next class" from confirmed meetings only. On the Dashboard it fetches its own data and stays quiet if that fails. */
export default function NextClassCard({ courses: provided }: { courses?: ScheduleCourse[] }) {
  const [fetched, setFetched] = useState<ScheduleCourse[] | null>(null);
  const [now, setNow] = useState(0);

  useEffect(() => {
    if (provided) return;
    const controller = new AbortController();
    getSchedule(controller.signal).then((data) => { if (!controller.signal.aborted) { setFetched(data.courses); setNow(Date.now()); } })
      .catch(() => { /* the Dashboard must not break because the schedule is unavailable */ });
    return () => controller.abort();
  }, [provided]);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);            // first reading happens after mount, never during rendering
    const timer = setInterval(tick, TICK_MS);
    return () => { clearTimeout(first); clearInterval(timer); };
  }, []);

  const courses = provided ?? fetched;
  if (!courses || now === 0) return null;
  const next = nextClass(courses, now);
  if (!next && provided) return null;              // on the schedule page itself the banner already says what to do
  const pending = courses.reduce((sum, course) => sum + course.suggestions.length, 0);

  return (
    <section className="next-class" aria-label="Next class">
      {next ? (
        <>
          <p className="next-class-label">{next.inProgress ? "Class in progress" : "Next class"}</p>
          <p className="next-class-title">
            <strong>{courseLabel(next.course)}</strong>
            <span> · {DAY_SHORT[next.day]} {formatRange(next.meeting.start_time, next.meeting.end_time)}</span>
            {next.meeting.location && <span> · {next.meeting.location}</span>}
          </p>
          <p className="next-class-when">{describeNext(next)}</p>
        </>
      ) : (
        <p className="next-class-title">
          {pending > 0 ? `${pending} class time${pending === 1 ? "" : "s"} found in your syllabi. ` : "Add your class times to see your next class. "}
          <Link href="/class-schedule" className="text-link">{pending > 0 ? "Review them" : "Open Class Schedule"}</Link>
        </p>
      )}
      {next && <Link href="/class-schedule" className="text-link">View schedule</Link>}
    </section>
  );
}
