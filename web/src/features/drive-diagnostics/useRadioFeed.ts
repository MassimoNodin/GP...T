"use client";

import { useEffect, useRef, useState } from "react";
import {
  createFeedPoller,
  fetchEventsPage,
  reduceFeed,
  type FeedState,
} from "./radio-feed";

/**
 * Live Radio History feed. Starts from the server-read state (identical SSR
 * output) and polls `/api/engineer/events?after=` while the tab is visible.
 * State lives in a ref as well as in React state so a catch-up poll that
 * fires before React re-renders still uses the newest cursor.
 */
export function useRadioFeed(initial: FeedState, enabled: boolean): FeedState {
  const [state, setState] = useState(initial);
  const stateRef = useRef(state);

  useEffect(() => {
    if (!enabled) return;
    const poller = createFeedPoller({
      fetchRead: (cursor, signal) => fetchEventsPage(cursor, signal),
      getCursor: () => stateRef.current.nextAfter,
      onRead: (read, cursor) => {
        const next = reduceFeed(stateRef.current, read, cursor);
        if (next === stateRef.current) return;
        stateRef.current = next;
        setState(next);
      },
    });
    const apply = () => poller.setHidden(document.hidden);
    apply();
    document.addEventListener("visibilitychange", apply);
    return () => {
      document.removeEventListener("visibilitychange", apply);
      poller.stop();
    };
  }, [enabled]);

  return state;
}
