"use client";

import * as Popover from "@radix-ui/react-popover";

/** Contextual "?" affordance for non-obvious concepts (anomali, stock velocity,
 * perkiraan laba). Headless Radix popover styled to the glass system — tap to
 * open, plain-language Indonesian explanation. */
export function HelpTip({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          aria-label={`Apa itu ${title}?`}
          className="ink-faint inline-flex h-[18px] w-[18px] items-center justify-center rounded-full text-[11px] font-bold normal-case tracking-normal transition-colors hover:text-accent-500"
          style={{ border: "1.5px solid currentColor" }}
        >
          ?
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={16}
          className="glass-card glass-strong z-50 max-w-xs animate-scale-in px-4 py-3 shadow-pop"
        >
          <p className="text-sm font-bold normal-case tracking-normal">{title}</p>
          <p className="ink-soft mt-1 text-[13px] font-normal normal-case leading-relaxed tracking-normal">
            {children}
          </p>
          <Popover.Arrow className="fill-[color:var(--glass-border)]" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
