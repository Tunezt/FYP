"use client";

import * as Popover from "@radix-ui/react-popover";

/** Contextual "?" affordance for non-obvious concepts (anomali, stock velocity,
 * perkiraan laba). Headless Radix popover styled to the float material — tap to
 * open, plain-language Indonesian explanation. */
export function HelpTip({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={`Apa itu ${title}?`}
          className="ink-faint inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold normal-case leading-none tracking-normal transition-colors hover:text-[color:var(--accent)] data-[state=open]:text-[color:var(--accent)]"
          style={{ boxShadow: "inset 0 0 0 1.25px currentColor" }}
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
          className="z-[60] max-w-[18rem] animate-scale-in rounded-2xl bg-[color:var(--surface-float)] px-4 py-3 shadow-pop"
        >
          <p className="text-sm font-semibold normal-case tracking-normal">{title}</p>
          <p className="ink-soft mt-1 text-[13px] font-normal normal-case leading-relaxed tracking-normal">
            {children}
          </p>
          <Popover.Arrow className="fill-[color:var(--surface-float)]" width={12} height={6} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
