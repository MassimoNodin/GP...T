"use client";

import { useEffect } from "react";

export default function ReferenceDisclosureOpener() {
  useEffect(() => {
    const openTarget = (rawId: string) => {
      let id: string;
      try {
        id = decodeURIComponent(rawId);
      } catch {
        return;
      }
      if (!id) return;

      const target = document.getElementById(id);
      if (!target) return;

      const disclosure = target.closest("details");
      if (disclosure instanceof HTMLDetailsElement && !disclosure.open) {
        disclosure.open = true;
        target.scrollIntoView({ block: "start" });
      }
    };

    const openHashTarget = () => openTarget(window.location.hash.slice(1));
    const reopenHashLink = (event: MouseEvent) => {
      const origin = event.target;
      if (!(origin instanceof Element)) return;

      const anchor = origin.closest("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;

      const destination = new URL(anchor.href, window.location.href);
      if (
        destination.origin === window.location.origin &&
        destination.pathname === window.location.pathname &&
        destination.search === window.location.search &&
        destination.hash
      ) {
        openTarget(destination.hash.slice(1));
      }
    };

    openHashTarget();
    window.addEventListener("hashchange", openHashTarget);
    document.addEventListener("click", reopenHashLink);
    return () => {
      window.removeEventListener("hashchange", openHashTarget);
      document.removeEventListener("click", reopenHashLink);
    };
  }, []);

  return null;
}
