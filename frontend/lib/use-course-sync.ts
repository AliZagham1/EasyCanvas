"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchCourseSync, isCourseSyncActive, requestCourseSync } from "./course-sync";
import type { CourseSyncStatus } from "./course-sync";

/** Observe backend-owned ingestion; changing pages never cancels the actual job. */
export function useCourseSync(onCompleted: () => void) {
  const [sync, setSync] = useState<CourseSyncStatus | null>(null);
  const [error, setError] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const observedRun = useRef<string | undefined>(undefined);
  const generation = useRef(0);
  const mounted = useRef(false);
  const requestController = useRef<AbortController | null>(null);

  const applyStatus = useCallback((status: CourseSyncStatus) => {
    setSync(status);
    setError(false);
    if (isCourseSyncActive(status)) observedRun.current = status.run_id;
    if (status.status === "completed" && status.run_id && observedRun.current === status.run_id) {
      observedRun.current = undefined;
      onCompleted();
    }
  }, [onCompleted]);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let delay = 15000;
      const revision = generation.current;
      try {
        const status = await fetchCourseSync(controller.signal);
        if (controller.signal.aborted || revision !== generation.current) return;
        applyStatus(status);
        delay = isCourseSyncActive(status) ? 2000 : 15000;
      } catch (error) {
        if (!controller.signal.aborted && revision === generation.current) {
          console.error("Unable to load Canvas course sync:", error);
          setError(true);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, delay);
      }
    }
    void poll();
    return () => {
      mounted.current = false;
      generation.current += 1;
      controller.abort();
      requestController.current?.abort();
      clearTimeout(timer);
    };
  }, [applyStatus]);

  const startSync = useCallback(async () => {
    generation.current += 1; // Ignore a GET response that predates this POST.
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setRequesting(true);
    try {
      const status = await requestCourseSync(controller.signal);
      if (mounted.current && !controller.signal.aborted) applyStatus(status);
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) {
        console.error("Unable to start Canvas course sync:", error);
        setError(true);
      }
    } finally {
      if (mounted.current && !controller.signal.aborted) setRequesting(false);
    }
  }, [applyStatus]);

  return { sync, error, busy: requesting || (!error && isCourseSyncActive(sync)), startSync };
}
