"use client";

import { useId, useRef, useState, type CSSProperties, type KeyboardEvent, type MutableRefObject } from "react";
import { cn } from "@/lib/cn";

type DiagnosisId = "terms" | "edges" | "xray" | "translation";

interface DiagnosisItem {
  id: DiagnosisId;
  glyph: "T" | "e" | "x" | "t";
  term: string;
  title: string;
  teaser: string;
  body: string;
  detail: string;
  color: string;
  softColor: string;
  anchorClassName: string;
  noteClassName: string;
  segments: string[];
}

const diagnosisItems: DiagnosisItem[] = [
  {
    id: "terms",
    glyph: "T",
    term: "Terms",
    title: "词义停顿",
    teaser: "词义离开语境",
    body: "查到释义，却接不上当前语境。",
    detail: "短语、搭配和上下文没有被一起处理。",
    color: "#D49A18",
    softColor: "rgba(212, 154, 24, 0.24)",
    anchorClassName: "left-[30%] top-[39%]",
    noteClassName: "left-[3%] top-[1.25rem]",
    segments: [
      "left-[30%] top-[17%] h-[22%] w-px",
      "left-[11%] top-[17%] h-px w-[19%]",
    ],
  },
  {
    id: "edges",
    glyph: "e",
    term: "Edges",
    title: "结构边界",
    teaser: "结构边界断开",
    body: "从句、修饰、指代关系没有显出来。",
    detail: "大意能猜，但语法关系没有真正接上。",
    color: "#746694",
    softColor: "rgba(116, 102, 148, 0.18)",
    anchorClassName: "left-[43%] top-[62%]",
    noteClassName: "bottom-[0.75rem] left-[11%]",
    segments: [
      "left-[43%] top-[62%] h-[17%] w-px",
      "left-[20%] top-[79%] h-px w-[23%]",
    ],
  },
  {
    id: "xray",
    glyph: "x",
    term: "X-ray",
    title: "长句透视",
    teaser: "长句主干失焦",
    body: "句子太长时，真正丢失的往往不是单词，而是主干。",
    detail: "插入语、从句和修饰语把阅读顺序打散。",
    color: "#557B5C",
    softColor: "rgba(85, 123, 92, 0.18)",
    anchorClassName: "left-[58%] top-[40%]",
    noteClassName: "right-[2%] top-[1rem]",
    segments: [
      "left-[58%] top-[19%] h-[21%] w-px",
      "left-[58%] top-[19%] h-px w-[27%]",
    ],
  },
  {
    id: "translation",
    glyph: "t",
    term: "Translation",
    title: "译文依赖",
    teaser: "译文替代阅读",
    body: "中文看懂了，英文却没有真正读过。",
    detail: "整段翻译直接给出答案，却跳过了英文理解过程。",
    color: "#4F89B3",
    softColor: "rgba(79, 137, 179, 0.18)",
    anchorClassName: "left-[73%] top-[60%]",
    noteClassName: "bottom-[0.75rem] right-[5%]",
    segments: [
      "left-[73%] top-[60%] h-[20%] w-px",
      "left-[73%] top-[80%] h-px w-[18%]",
    ],
  },
];

export function TextDiagnosisPlate() {
  const [activeId, setActiveId] = useState<DiagnosisId>("xray");
  const desktopBranchRefs = useRef<Record<DiagnosisId, HTMLButtonElement | null>>({
    terms: null,
    edges: null,
    xray: null,
    translation: null,
  });
  const mobileBranchRefs = useRef<Record<DiagnosisId, HTMLButtonElement | null>>({
    terms: null,
    edges: null,
    xray: null,
    translation: null,
  });
  const baseId = useId();

  const activate = (id: DiagnosisId) => setActiveId(id);

  const handleBranchKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    id: DiagnosisId,
    refs: MutableRefObject<Record<DiagnosisId, HTMLButtonElement | null>>,
  ) => {
    if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(event.key)) {
      return;
    }

    event.preventDefault();

    const currentIndex = diagnosisItems.findIndex((item) => item.id === id);
    const lastIndex = diagnosisItems.length - 1;
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? lastIndex
          : event.key === "ArrowRight" || event.key === "ArrowDown"
            ? (currentIndex + 1) % diagnosisItems.length
            : (currentIndex - 1 + diagnosisItems.length) % diagnosisItems.length;
    const nextId = diagnosisItems[nextIndex].id;

    setActiveId(nextId);
    refs.current[nextId]?.focus();
  };

  return (
    <div
      className="relative mt-10 text-ink sm:mt-12 lg:mt-14"
      aria-label="英文阅读卡点诊断"
      style={
        {
          "--diagnosis-guide": "rgba(71, 174, 162, 0.68)",
          "--diagnosis-guide-soft": "rgba(71, 174, 162, 0.34)",
        } as CSSProperties
      }
    >
      <DesktopDiagnosisPlate
        activeId={activeId}
        baseId={baseId}
        onActivate={activate}
        onBranchKeyDown={(event, id) => handleBranchKeyDown(event, id, desktopBranchRefs)}
        branchRefs={desktopBranchRefs}
      />
      <MobileDiagnosisPlate
        activeId={activeId}
        baseId={baseId}
        onActivate={activate}
        onBranchKeyDown={(event, id) => handleBranchKeyDown(event, id, mobileBranchRefs)}
        branchRefs={mobileBranchRefs}
      />
    </div>
  );
}

interface DiagnosisPlateProps {
  activeId: DiagnosisId;
  baseId: string;
  onActivate: (id: DiagnosisId) => void;
  onBranchKeyDown: (event: KeyboardEvent<HTMLButtonElement>, id: DiagnosisId) => void;
  branchRefs: MutableRefObject<Record<DiagnosisId, HTMLButtonElement | null>>;
}

function DesktopDiagnosisPlate({ activeId, baseId, onActivate, onBranchKeyDown, branchRefs }: DiagnosisPlateProps) {
  return (
    <div className="relative hidden w-full overflow-hidden border-y border-hairline/80 px-4 @container md:block">
      <div className="relative mx-auto w-full max-w-[76rem] aspect-[1.9/1]">
        <TypographyGuideLines />

        <div className="absolute inset-x-0 top-[28%] z-10 flex justify-center">
          <div className="select-none font-headline text-[17.5cqw] font-normal leading-none tracking-[-0.035em] text-ink">
            {diagnosisItems.map((item) => (
              <LetterMark key={item.id} item={item} active={item.id === activeId} onClick={() => onActivate(item.id)} />
            ))}
          </div>
        </div>

        <div className="pointer-events-none absolute inset-0 z-20" role="group" aria-label="Text reading diagnosis">
          {diagnosisItems.map((item) => (
            <DesktopBranch
              key={item.id}
              item={item}
              active={item.id === activeId}
              buttonId={`${baseId}-${item.id}-button`}
              panelId={`${baseId}-${item.id}-panel`}
              buttonRef={(node) => {
                branchRefs.current[item.id] = node;
              }}
              onActivate={() => onActivate(item.id)}
              onKeyDown={(event) => onBranchKeyDown(event, item.id)}
            />
          ))}
        </div>

        <p className="pointer-events-none absolute bottom-4 left-1/2 z-30 -translate-x-1/2 text-xs leading-5 text-muted">
          点击字母切换诊断
        </p>
      </div>
    </div>
  );
}

function MobileDiagnosisPlate({ activeId, baseId, onActivate, onBranchKeyDown, branchRefs }: DiagnosisPlateProps) {
  return (
    <div className="md:hidden">
      <div className="relative min-h-[13rem] overflow-hidden border-y border-hairline/80">
        <span className="absolute inset-x-4 top-[35%] border-t border-dotted border-[var(--diagnosis-guide-soft)]" aria-hidden="true" />
        <span className="absolute inset-x-4 top-[52%] border-t border-dotted border-[var(--diagnosis-guide-soft)]" aria-hidden="true" />
        <span className="absolute inset-x-4 top-[68%] border-t border-[var(--diagnosis-guide-soft)]" aria-hidden="true" />
        <div
          className="absolute inset-x-0 top-[23%] flex justify-center select-none text-center font-headline text-[clamp(5.9rem,26vw,8rem)] font-normal leading-none tracking-[-0.035em] text-ink"
        >
          {diagnosisItems.map((item) => (
            <LetterMark key={item.id} item={item} active={item.id === activeId} onClick={() => onActivate(item.id)} />
          ))}
        </div>
      </div>

      <div className="mt-5 grid gap-3" role="group" aria-label="Text reading diagnosis">
        {diagnosisItems.map((item) => {
          const active = item.id === activeId;

          return (
            <div
              key={item.id}
              className={cn(
                "rounded-[0.7rem] border bg-surface-warm/70 px-3.5 py-3 transition-colors duration-200",
                active ? "border-[color-mix(in_srgb,var(--ink)_12%,var(--hairline))]" : "border-hairline/75",
              )}
              style={{ borderColor: active ? item.color : undefined }}
            >
              <BranchTabButton
                item={item}
                active={active}
                buttonId={`${baseId}-${item.id}-mobile-button`}
                panelId={`${baseId}-${item.id}-mobile-panel`}
                refCallback={(node) => {
                  branchRefs.current[item.id] = node;
                }}
                onActivate={() => onActivate(item.id)}
                onKeyDown={(event) => onBranchKeyDown(event, item.id)}
              />
              <div
                id={`${baseId}-${item.id}-mobile-panel`}
                role="region"
                aria-labelledby={`${baseId}-${item.id}-mobile-button`}
                hidden={!active}
                className="mt-3 border-t border-hairline/70 pt-3 text-sm leading-6 text-muted"
              >
                <p className="font-medium text-ink">{item.body}</p>
                <p className="mt-1">{item.detail}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TypographyGuideLines() {
  return (
    <div className="pointer-events-none absolute inset-0 z-0" aria-hidden="true">
      <span className="absolute inset-x-[5%] top-[30%] border-t border-dotted border-[var(--diagnosis-guide)]" />
      <span className="absolute inset-x-[5%] top-[47%] border-t border-dotted border-[var(--diagnosis-guide-soft)]" />
      <span className="absolute inset-x-[5%] top-[63%] border-t border-[var(--diagnosis-guide-soft)]" />
      <span className="absolute left-[5%] top-[30%] text-[0.68rem] leading-none text-muted">cap height</span>
      <span className="absolute right-[6%] top-[47%] text-[0.68rem] leading-none text-muted">x-height</span>
      <span className="absolute left-[5%] top-[64.5%] text-[0.68rem] leading-none text-muted">baseline</span>
      <span className="absolute left-[24%] top-[21%] h-[51%] w-px bg-[var(--diagnosis-guide-soft)]" />
      <span className="absolute left-[39%] top-[31%] h-[40%] w-px bg-[var(--diagnosis-guide-soft)]" />
      <span className="absolute left-[57%] top-[24%] h-[47%] w-px bg-[var(--diagnosis-guide-soft)]" />
      <span className="absolute left-[73%] top-[26%] h-[47%] w-px bg-[var(--diagnosis-guide-soft)]" />
    </div>
  );
}

function DesktopBranch({
  item,
  active,
  buttonId,
  panelId,
  buttonRef,
  onActivate,
  onKeyDown,
}: {
  item: DiagnosisItem;
  active: boolean;
  buttonId: string;
  panelId: string;
  buttonRef: (node: HTMLButtonElement | null) => void;
  onActivate: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const guideColor = active ? item.color : "rgba(71, 174, 162, 0.48)";

  return (
    <>
      {item.segments.map((segment) => (
        <span
          key={segment}
          className={cn("absolute block transition-colors duration-200", segment)}
          style={{ backgroundColor: guideColor }}
          aria-hidden="true"
        />
      ))}
      <span
        className={cn(
          "absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border transition-colors duration-200",
          active ? "border-transparent" : "border-[var(--diagnosis-guide)] bg-[#F7F5F0]",
          item.anchorClassName,
        )}
        style={{ backgroundColor: active ? item.color : undefined }}
        aria-hidden="true"
      />
      <div
        className={cn(
          "pointer-events-auto absolute w-[min(19rem,29vw)] transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]",
          active ? "z-30 opacity-100" : "z-20 opacity-90 hover:opacity-100",
          item.noteClassName,
        )}
      >
        <div
          className={cn(
            "border bg-[#FBFAF6]/90 shadow-[0_1px_2px_rgba(23,21,17,0.035)] transition-colors duration-200",
            active ? "rounded-[0.75rem] px-4 py-3.5" : "rounded-[0.6rem] px-3 py-2.5",
          )}
          style={{
            borderColor: active ? item.color : "rgba(217, 209, 195, 0.84)",
          }}
        >
          <BranchTabButton
            item={item}
            active={active}
            buttonId={buttonId}
            panelId={panelId}
            refCallback={buttonRef}
            onActivate={onActivate}
            onKeyDown={onKeyDown}
          />
          <div
            id={panelId}
            role="region"
            aria-labelledby={buttonId}
            hidden={!active}
            className="mt-3 border-t border-hairline/70 pt-3 text-sm leading-6 text-muted"
          >
            <p className="font-medium text-ink">{item.body}</p>
            <p className="mt-1">{item.detail}</p>
          </div>
        </div>
      </div>
    </>
  );
}

function BranchTabButton({
  item,
  active,
  buttonId,
  panelId,
  refCallback,
  onActivate,
  onKeyDown,
}: {
  item: DiagnosisItem;
  active: boolean;
  buttonId: string;
  panelId: string;
  refCallback: (node: HTMLButtonElement | null) => void;
  onActivate: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      id={buttonId}
      ref={refCallback}
      type="button"
      aria-expanded={active}
      aria-controls={panelId}
      onClick={onActivate}
      onKeyDown={onKeyDown}
      className="group flex w-full min-h-11 items-start gap-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20"
    >
      <span
        className="mt-1 inline-flex size-2.5 shrink-0 rounded-full border transition-colors duration-200"
        style={{
          backgroundColor: active ? item.color : "transparent",
          borderColor: item.color,
        }}
        aria-hidden="true"
      />
      <span className="min-w-0">
        <span className="block text-xs font-semibold leading-none tracking-[0.06em]" style={{ color: item.color }}>
          {item.glyph} · {item.term}
        </span>
        <span className="mt-1.5 block font-headline text-[1.05rem] font-semibold leading-tight text-ink">
          {item.title}
        </span>
        <span
          className={cn(
            "mt-1 block text-xs leading-5 text-muted transition-colors duration-200",
            "group-hover:text-ink-soft group-focus-visible:text-ink-soft",
          )}
        >
          {item.teaser}
        </span>
      </span>
    </button>
  );
}

function LetterMark({ item, active, onClick }: { item: DiagnosisItem; active: boolean; onClick?: () => void }) {
  const Component = onClick ? "button" : "span";
  return (
    <Component
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "relative inline-block transition-colors duration-200",
        onClick && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20 rounded-md",
        active ? "text-ink" : "text-ink/80 hover:text-ink"
      )}
      aria-pressed={onClick ? active : undefined}
      aria-label={onClick ? `${item.term}: ${item.title}` : undefined}
    >
      <span className="relative z-10">{item.glyph}</span>
      <span
        className={cn(
          "absolute inset-x-[0.06em] bottom-[0.08em] z-0 h-[0.085em] origin-left rounded-full transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]",
          active ? "scale-x-100 opacity-100" : "scale-x-0 opacity-0",
        )}
        style={{ backgroundColor: item.softColor }}
        aria-hidden="true"
      />
    </Component>
  );
}
