"use client";

import { useParams } from "next/navigation";
import type { ReactNode } from "react";
import { ErpShell } from "@/components/erp";

export default function ErpLayout({ children }: { children: ReactNode }) {
  const { accessId } = useParams<{ accessId: string }>();
  return <ErpShell accessId={accessId}>{children}</ErpShell>;
}
