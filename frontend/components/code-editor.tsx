"use client";

import Editor from "@monaco-editor/react";
import { useTheme } from "next-themes";

export function CodeEditor({
  language,
  value,
  onChange,
  readOnly = false,
}: {
  language: string;
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
}) {
  const { resolvedTheme } = useTheme();

  return (
    <Editor
      height="100%"
      language={language}
      value={value}
      onChange={(nextValue) => onChange?.(nextValue ?? "")}
      theme={resolvedTheme === "dark" ? "vs-dark" : "light"}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        wordWrap: "on",
        readOnly,
      }}
    />
  );
}
