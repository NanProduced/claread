import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

type BrandLockupProps = {
  href: Route | null;
  className?: string;
  imageClassName?: string;
  priority?: boolean;
};

export function BrandLockup({
  href,
  className,
  imageClassName,
  priority = false,
}: BrandLockupProps) {
  const mark = (
    <span role="img" aria-label="Claread 透读" className="block">
      <Image
        src="/brand/claread-horizontal-bilingual.png"
        alt=""
        aria-hidden="true"
        width={328}
        height={96}
        priority={priority}
        className={cx("h-auto w-44 sm:w-56 dark:hidden", imageClassName)}
      />
      <Image
        src="/brand/claread-horizontal-bilingual-reversed.png"
        alt=""
        aria-hidden="true"
        width={328}
        height={96}
        priority={priority}
        className={cx("hidden h-auto w-44 sm:w-56 dark:block", imageClassName)}
      />
    </span>
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
    <>
      <Image
        src="/brand/claread-icon-fullcolor.png"
        alt=""
        aria-hidden="true"
        width={size}
        height={size}
        className={cx(
          "brand-aperture-mark pointer-events-none select-none dark:hidden",
          className,
        )}
      />
      <Image
        src="/brand/claread-icon-reversed.png"
        alt=""
        aria-hidden="true"
        width={size}
        height={size}
        className={cx(
          "brand-aperture-mark pointer-events-none hidden select-none dark:block",
          className,
        )}
      />
    </>
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
        className="brand-aperture-mark h-4 w-4 dark:hidden"
      />
      <Image
        src="/brand/claread-icon-reversed.png"
        alt=""
        aria-hidden="true"
        width={18}
        height={18}
        className="brand-aperture-mark hidden h-4 w-4 dark:block"
      />
      <span>{label}</span>
    </div>
  );
}
