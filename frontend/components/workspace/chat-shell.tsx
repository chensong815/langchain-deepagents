"use client";

import type { ReactNode, RefObject } from "react";

type ChatShellProps = {
  bodyRef: RefObject<HTMLDivElement | null>;
  composer: ReactNode;
  messages: ReactNode;
  onScroll: () => void;
  summaryModalTrigger: ReactNode;
  title: string;
  toolbar: ReactNode;
};

export function ChatShell({
  bodyRef,
  composer,
  messages,
  onScroll,
  summaryModalTrigger,
  title,
  toolbar,
}: ChatShellProps) {
  return (
    <main className="panel relative flex h-[calc(100dvh-6.2rem)] flex-1 self-start flex-col overflow-hidden">
      <div className="border-b border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel-strong)_74%,transparent)] px-5 py-2 backdrop-blur-xl">
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          <div>
            <h1 className="max-w-[56rem] truncate text-[1rem] font-semibold tracking-[0.005em]">{title}</h1>
            {summaryModalTrigger}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">{toolbar}</div>
        </div>
      </div>

      <div
        className="flex-1 overflow-y-auto px-4 py-4.5 md:px-5"
        onScroll={onScroll}
        ref={bodyRef}
      >
        <div className="mx-auto max-w-[1080px] space-y-4.5">
          <div className="space-y-4">{messages}</div>
        </div>
      </div>

      <div className="border-t border-[var(--line)] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_82%,transparent),color-mix(in_srgb,var(--panel)_96%,transparent))] px-3 py-2 backdrop-blur-xl md:px-5 md:py-3">
        <section className="mx-auto max-w-[1200px]">{composer}</section>
      </div>
    </main>
  );
}
