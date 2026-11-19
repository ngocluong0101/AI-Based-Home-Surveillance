// What the WebSocket does in the demo: every second a camera.status for each camera, and now and then a person is detected.
import type { QueryClient } from "@tanstack/react-query";
import type { PersonEvent } from "../types";
import { applyMessage } from "../websocket/messages";
import { liveStatuses, newLiveEvent, settleVerification } from "./server";

const VLM_ANSWERS_AFTER_MS = 3000;

const STATUS_EVERY_MS = 1000;
const FIRST_EVENT_MS = 9000; // soon enough for a visitor to see a toast
const NEXT_EVENT_MS = [14000, 32000];
const RETRY_MS = 4000; // nobody was in view when the timer fired

/** Starts the pretend socket; the returned function stops it. */
export function startDemoRealtime(qc: QueryClient, onNewEvent: (event: PersonEvent) => void): () => void {
  const push = () => {
    for (const data of liveStatuses()) applyMessage(qc, { type: "camera.status", data });
  };
  push();
  const statusTimer = window.setInterval(push, STATUS_EVERY_MS);

  let eventTimer = 0;
  const scheduleEvent = (delay: number) => {
    eventTimer = window.setTimeout(() => {
      const event = newLiveEvent();
      if (event) {
        applyMessage(qc, { type: "event.created", data: { ...event } }, onNewEvent);
        if (event.verification === "pending") {
          // the pretend VLM answers a few seconds later, like the real one: the card changes without a reload
          window.setTimeout(() => {
            applyMessage(qc, { type: "event.updated", data: { ...settleVerification(event, Math.random) } });
          }, VLM_ANSWERS_AFTER_MS);
        }
        scheduleEvent(NEXT_EVENT_MS[0] + Math.random() * (NEXT_EVENT_MS[1] - NEXT_EVENT_MS[0]));
      } else {
        scheduleEvent(RETRY_MS);
      }
    }, delay);
  };
  scheduleEvent(FIRST_EVENT_MS);

  return () => {
    window.clearInterval(statusTimer);
    window.clearTimeout(eventTimer);
  };
}
