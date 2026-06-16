import { type ComponentPropsWithoutRef, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

interface BentoGridProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
  className?: string;
}

interface BentoCardProps extends ComponentPropsWithoutRef<"div"> {
  name: string;
  className: string;
  background: ReactNode;
  Icon: React.ElementType;
  description: string;
  href?: string;
  cta?: string;
}

const BentoGrid = ({ children, className, ...props }: BentoGridProps) => {
  return (
    <div
      className={cn(
        "grid w-full auto-rows-[22rem] grid-cols-3 gap-4",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
};

const BentoCard = ({
  name,
  className,
  background,
  Icon,
  description,
  href,
  cta,
  ...props
}: BentoCardProps) => (
  <div
    key={name}
    className={cn(
      "group relative col-span-3 flex flex-col justify-between overflow-hidden rounded-xl",
      // light styles
      "bg-background [box-shadow:0_0_0_1px_rgba(0,0,0,.03),0_2px_4px_rgba(0,0,0,.05),0_12px_24px_rgba(0,0,0,.05)]",
      // dark styles
      "dark:bg-background transform-gpu dark:[box-shadow:0_-20px_80px_-20px_#ffffff1f_inset] dark:[border:1px_solid_rgba(255,255,255,.1)]",
      className,
    )}
    {...props}
  >
    <div>{background}</div>
    <div className="relative z-10 mt-auto p-6 sm:p-7">
      <div className="pointer-events-none flex transform-gpu flex-col gap-2 transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] lg:group-hover:-translate-y-1">
        <span className="mb-1 flex h-9 w-9 items-center justify-center rounded-lg border border-ink/10 bg-white/72 text-ink shadow-[0_1px_0_rgba(255,255,255,0.85)]">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <h3 className="text-xl font-semibold tracking-[-0.01em] text-ink dark:text-neutral-300">
          {name}
        </h3>
        <p className="max-w-lg text-sm leading-6 text-muted">{description}</p>
      </div>

      {href && cta ? (
        <div
          className={cn(
            "pointer-events-none flex w-full translate-y-0 transform-gpu flex-row items-center transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 lg:hidden",
          )}
        >
          <Button
            variant="link"
            asChild
            size="sm"
            className="pointer-events-auto p-0"
          >
            <a href={href}>
              {cta}
              <ArrowRight className="ms-2 h-4 w-4 rtl:rotate-180" />
            </a>
          </Button>
        </div>
      ) : null}
    </div>

    {href && cta ? (
      <div
        className={cn(
          "pointer-events-none absolute bottom-0 z-10 hidden w-full translate-y-10 transform-gpu flex-row items-center p-6 opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 lg:flex",
        )}
      >
        <Button
          variant="link"
          asChild
          size="sm"
          className="pointer-events-auto p-0"
        >
          <a href={href}>
            {cta}
            <ArrowRight className="ms-2 h-4 w-4 rtl:rotate-180" />
          </a>
        </Button>
      </div>
    ) : null}

    <div className="pointer-events-none absolute inset-0 transform-gpu transition-colors duration-300 group-hover:bg-ink/[0.012] group-hover:dark:bg-neutral-800/10" />
  </div>
);

export { BentoCard, BentoGrid };
