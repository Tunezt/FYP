"use client";

import { useEffect, useRef } from "react";
import { useSectionSpy } from "@/components/SectionIndex";
import { SETTINGS_GROUPS, SETTINGS_INDEX } from "@/lib/settings-sections";

/** The Pengaturan sections, nested under "Pengaturan" in the sidebar while
 * that page is open: grouped, follows the reading, a click scrolls there.
 * Only shown where the sidebar is full width (xl); narrower screens get the
 * chip row at the top of the page instead. */
export function SettingsSubnav() {
  const { current, go } = useSectionSpy(SETTINGS_INDEX);
  const ref = useRef<HTMLElement>(null);

  // The sidebar scrolls on a short screen: keep the marked section in view.
  useEffect(() => {
    const link = ref.current?.querySelector<HTMLElement>(`[data-id="${current}"]`);
    const box = link?.closest<HTMLElement>(".shell-scroll");
    if (!link || !box) return;
    const top = link.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    if (top < box.scrollTop + 8 || top + link.offsetHeight > box.scrollTop + box.clientHeight - 8) {
      box.scrollTo({ top: top - box.clientHeight / 2, behavior: "smooth" });
    }
  }, [current]);

  // On a short screen the list can sit below the fold when the page opens:
  // bring it up, but never past the Pengaturan link it hangs from.
  useEffect(() => {
    const nav = ref.current;
    const box = nav?.closest<HTMLElement>(".shell-scroll");
    if (!nav || !box) return;
    const hidden = nav.getBoundingClientRect().bottom - box.getBoundingClientRect().bottom;
    const room = nav.getBoundingClientRect().top - 56 - box.getBoundingClientRect().top;
    if (hidden > 0) box.scrollTop += Math.max(0, Math.min(hidden + 8, room));
  }, []);

  return (
    <nav ref={ref} aria-label="Bagian Pengaturan" className="subnav hidden xl:block">
      {SETTINGS_GROUPS.map((g) => (
        <div key={g.label} role="group" aria-labelledby={`subnav-${g.label}`}>
          <p id={`subnav-${g.label}`} className="subnav-label">
            {g.label}
          </p>
          <ul>
            {g.entries.map((e) => (
              <li key={e.id}>
                <a
                  href={`/settings#${e.id}`}
                  data-id={e.id}
                  aria-current={e.id === current ? "location" : undefined}
                  onClick={(ev) => {
                    ev.preventDefault();
                    go(e.id);
                  }}
                  className="subnav-link"
                >
                  {e.label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
