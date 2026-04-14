"use client";

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

interface GradeCardProps {
  grade: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const gradeColors = {
  "A+": "bg-emerald-500/10 text-emerald-500 border-emerald-500/30",
  "A": "bg-green-500/10 text-green-500 border-green-500/30",
  "B": "bg-lime-500/10 text-lime-500 border-lime-500/30",
  "C": "bg-yellow-500/10 text-yellow-500 border-yellow-500/30",
  "D": "bg-orange-500/10 text-orange-500 border-orange-500/30",
  "D-": "bg-orange-400/10 text-orange-400 border-orange-400/30",
  "F": "bg-red-500/10 text-red-500 border-red-500/30",
};

const gradeSizes = {
  sm: "text-sm px-2 py-1",
  md: "text-base px-3 py-1.5",
  lg: "text-xl px-4 py-2",
};

export function GradeCard({ grade, size = "md", className }: GradeCardProps) {
  const colorClass = gradeColors[grade as keyof typeof gradeColors] || gradeColors["C"];
  const sizeClass = gradeSizes[size];

  return (
    <div
      className={cn(
        "inline-flex items-center justify-center font-bold rounded-md border",
        colorClass,
        sizeClass,
        className
      )}
    >
      {grade}
    </div>
  );
}
