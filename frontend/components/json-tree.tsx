"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

function JsonNode({ label, value, depth = 0 }: { label?: string; value: unknown; depth?: number }) {
  const isObject = typeof value === "object" && value !== null;
  const isArray = Array.isArray(value);
  const [open, setOpen] = useState(depth < 1);

  if (!isObject) {
    return (
      <div className="flex gap-2 text-xs leading-6">
        {label ? <span className="text-[var(--muted)]">{label}:</span> : null}
        <span className="whitespace-pre-wrap break-all text-[var(--foreground)]">{String(value)}</span>
      </div>
    );
  }

  const entries = isArray
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value as Record<string, unknown>);

  return (
    <div className="space-y-1 text-xs">
      <button
        className="flex items-center gap-1 text-[var(--muted)] transition hover:text-[var(--foreground)]"
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <span>{label ?? (isArray ? "array" : "object")}</span>
      </button>
      {open ? (
        <div className="space-y-1 border-l border-[var(--line)] pl-3">
          {entries.map(([entryKey, entryValue]) => (
            <JsonNode key={entryKey} label={entryKey} value={entryValue} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function JsonTree({ value }: { value: unknown }) {
  return <JsonNode value={value} />;
}
