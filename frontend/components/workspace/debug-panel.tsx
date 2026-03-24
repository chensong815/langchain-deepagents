"use client";

import type { ReactNode } from "react";

type DebugPanelProps = {
  children: ReactNode;
  open: boolean;
};

export function DebugPanel({ children, open }: DebugPanelProps) {
  if (!open) {
    return null;
  }
  return <>{children}</>;
}
