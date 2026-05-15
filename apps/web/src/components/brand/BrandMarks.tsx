import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

type BrandLockupProps = {
  href?: Route | null;
  className?: string;
  imageClassName?: string;
  priority?: boolean;
};

export function BrandLockup({
  href = "/daily" as Route,
  className,
  imageClassName,
  priority = false,
}: BrandLockupProps) {
  const mark = (
    <Image
      src="/brand/claread-horizontal-bilingual.png"
      alt="Claread 透读"
      width={328}
      height={96}
      priority={priority}
      className={cx("h-auto w-44 sm:w-56", imageClassName)}
    />
  );

  if (!href) {
    return <div className={className}>{mark}</div>;
  }

  return (
    <Link href={href} className={cx("focus-ring rounded-note", className)}>
      {mark}
    </Link>
  );
}

type ApertureWatermarkProps = {
  className?: string;
  size?: number;
};

export function ApertureWatermark({ className, size = 320 }: ApertureWatermarkProps) {
  return (
    <Image
      src="/brand/claread-icon-fullcolor.png"
      alt=""
      aria-hidden="true"
      width={size}
      height={size}
      className={cx("pointer-events-none select-none", className)}
    />
  );
}

type ClareadStampProps = {
  className?: string;
  label?: string;
};

export function ClareadStamp({ className, label = "CLAREAD EDITION" }: ClareadStampProps) {
  return (
    <div
      className={cx(
        "inline-flex items-center gap-2 rounded-pill border border-lens-blue/30 bg-reader-paper/80 px-3 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.16em] text-lens-blue",
        className,
      )}
    >
      <Image
        src="/brand/claread-icon-fullcolor.png"
        alt=""
        aria-hidden="true"
        width={18}
        height={18}
        className="h-4 w-4"
      />
      <span>{label}</span>
    </div>
  );
}
