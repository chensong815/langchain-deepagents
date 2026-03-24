"use client";

import type { ReactNode } from "react";

type SkillsPanelProps = {
  cardsPane: ReactNode;
  cardsResizeHandle: ReactNode;
  editorPane: ReactNode;
};

export function SkillsPanel({
  cardsPane,
  cardsResizeHandle,
  editorPane,
}: SkillsPanelProps) {
  return (
    <>
      {cardsPane}
      {cardsResizeHandle}
      {editorPane}
    </>
  );
}
