import type {
  DailyReaderArticle,
  DailyReaderCheckpoint,
  DailyReaderLanguageTarget,
  DailyReaderReadingUnit,
  DailyReaderSentenceMap,
  DailyReaderTransferTask,
} from "@/types/view/DailyReaderVm";

/* ---------- 排版基元（C-2 token 语法，mono 小标签 + 细 rule） ---------- */

const monoLabel =
  "dr-font-mono text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)]";
const monoAccent =
  "dr-font-mono text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-accent)]";
const zhBody =
  "dr-font-zh text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]";
const zhMeta =
  "dr-font-zh text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]";
const enBody =
  "dr-font-en text-[length:var(--dr-type-body-size)] leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]";

function SectionHeading({ zh, en, count }: { zh: string; en: string; count?: number }) {
  return (
    <div className="mb-8 flex items-baseline gap-3 border-t border-[color:var(--dr-rule)] pt-10">
      <h2 className="dr-font-zh text-[length:var(--dr-type-headline-size)] font-normal leading-[var(--dr-type-headline-lh)] text-[color:var(--dr-ink-zh)]">
        {zh}
      </h2>
      <span className={monoLabel}>{en}</span>
      {typeof count === "number" ? <span className={monoLabel}>{count}</span> : null}
      <span aria-hidden="true" className="h-px flex-1 self-center bg-[var(--dr-rule)]" />
    </div>
  );
}

/* ---------- 正文单元：译文按需展开（零 JS，原生 details） ---------- */

function UnitTranslation({ translation }: { translation: string }) {
  return (
    <details className="group mt-3">
      <summary className="focus-ring inline-flex min-h-8 cursor-pointer list-none items-center gap-2 text-[color:var(--dr-meta)] transition-colors hover:text-[color:var(--dr-accent)] [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className={`${monoAccent} transition-transform group-open:rotate-45`}>
          +
        </span>
        <span className={monoLabel}>译文</span>
      </summary>
      <p className={`${zhBody} mt-2 border-t border-[color:var(--dr-rule)] pt-3`}>{translation}</p>
    </details>
  );
}

function SentenceMapCard({ map }: { map: DailyReaderSentenceMap }) {
  return (
    <details className="group mt-4">
      <summary className="focus-ring inline-flex min-h-8 cursor-pointer list-none items-center gap-2 text-[color:var(--dr-meta)] transition-colors hover:text-[color:var(--dr-accent)] [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className={`${monoAccent} transition-transform group-open:rotate-45`}>
          +
        </span>
        <span className={monoLabel}>长难句精讲</span>
        {map.complexityKind ? (
          <span className={monoLabel}>
            {map.complexityKind === "complex_syntax" ? "复杂句法" : "论证结构"}
          </span>
        ) : null}
      </summary>
      <div className="mt-3 space-y-4 border-t border-[color:var(--dr-rule)] pt-4">
        <p className={`${enBody} italic`}>&ldquo;{map.sentence}&rdquo;</p>
        <div>
          <p className={monoLabel}>译文</p>
          <p className={`${zhBody} mt-1`}>{map.translation}</p>
        </div>
        {map.teachingPurpose ? (
          <div>
            <p className={monoLabel}>拆解</p>
            <p className={`${zhMeta} mt-1`}>{map.teachingPurpose}</p>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function ReadingUnitView({
  unit,
  sentenceMap,
}: {
  unit: DailyReaderReadingUnit;
  sentenceMap?: DailyReaderSentenceMap;
}) {
  return (
    <div className="mt-8">
      <p className={enBody}>{unit.text}</p>
      {unit.translation ? <UnitTranslation translation={unit.translation} /> : null}
      {sentenceMap ? <SentenceMapCard map={sentenceMap} /> : null}
    </div>
  );
}

/* ---------- 文章结构提纲 ---------- */

function StructureOutline({ article }: { article: DailyReaderArticle }) {
  if (article.structureMap.length === 0) return null;
  return (
    <section className="mt-16">
      <SectionHeading zh="文章结构" en="STRUCTURE" count={article.structureMap.length} />
      <ol className="space-y-6">
        {article.structureMap.map((node, index) => (
          <li key={`${node.label}-${index}`} className="flex gap-4">
            <span aria-hidden="true" className={`${monoAccent} shrink-0`}>
              {String(index + 1).padStart(2, "0")}
            </span>
            <div>
              <p className={`${zhBody} font-semibold`}>{node.label}</p>
              <p className={`${zhMeta} mt-1`}>{node.role}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

/* ---------- 语言精讲 ---------- */

function LanguageTargetCard({ target }: { target: DailyReaderLanguageTarget }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="dr-font-en text-[length:var(--dr-type-body-size)] font-semibold leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]">
          {target.expression}
        </p>
        {target.targetKind ? <span className={monoLabel}>{target.targetKind}</span> : null}
      </div>
      <p className={`${zhBody} mt-1`}>{target.meaningZh}</p>
      {target.usageNote ? <p className={`${zhMeta} mt-2`}>{target.usageNote}</p> : null}
      {target.reusablePattern ? (
        <div className="mt-3 border-t border-[color:var(--dr-rule)] pt-2">
          <p className={monoLabel}>可复用句型</p>
          <p className="dr-font-en mt-1 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink)]">
            {target.reusablePattern}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function LanguageTargetsSection({ article }: { article: DailyReaderArticle }) {
  if (article.languageTargets.length === 0) return null;
  return (
    <section className="mt-16">
      <SectionHeading zh="语言精讲" en="LANGUAGE" count={article.languageTargets.length} />
      <div className="grid gap-x-10 gap-y-8 sm:grid-cols-2">
        {article.languageTargets.map((target, index) => (
          <LanguageTargetCard key={`${target.expression}-${index}`} target={target} />
        ))}
      </div>
    </section>
  );
}

/* ---------- 证据型自测 ---------- */

function CheckpointItem({
  checkpoint,
  index,
}: {
  checkpoint: DailyReaderCheckpoint;
  index: number;
}) {
  return (
    <details className="group border-t border-[color:var(--dr-rule)] py-5">
      <summary className="focus-ring cursor-pointer list-none [&::-webkit-details-marker]:hidden">
        <div className="flex items-start gap-4">
          <span
            aria-hidden="true"
            className={`${monoAccent} mt-1 shrink-0 transition-transform group-open:rotate-45`}
          >
            +
          </span>
          <div>
            <span className={monoLabel}>
              自测 {String(index + 1).padStart(2, "0")}
              {checkpoint.skill ? ` · ${checkpoint.skill}` : ""}
            </span>
            <p className={`${zhBody} mt-1`}>{checkpoint.prompt}</p>
            {checkpoint.promptSubject ? (
              <p className="dr-font-en mt-2 text-[length:var(--dr-type-zh-size)] italic leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink)]">
                &ldquo;{checkpoint.promptSubject}&rdquo;
              </p>
            ) : null}
          </div>
        </div>
      </summary>
      <div className="mt-4 space-y-3 pl-9">
        <div>
          <p className={monoLabel}>参考答案</p>
          <p className={`${zhBody} mt-1`}>{checkpoint.referenceAnswer}</p>
        </div>
        {checkpoint.answerSubject ? (
          <div>
            <p className={monoLabel}>答案落点</p>
            <p className="dr-font-en mt-1 text-[length:var(--dr-type-zh-size)] italic leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink)]">
              &ldquo;{checkpoint.answerSubject}&rdquo;
            </p>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function CheckpointsSection({ article }: { article: DailyReaderArticle }) {
  if (article.checkpoints.length === 0) return null;
  return (
    <section className="mt-16">
      <SectionHeading zh="证据自测" en="CHECKPOINTS" count={article.checkpoints.length} />
      <div>
        {article.checkpoints.map((checkpoint, index) => (
          <CheckpointItem key={`cp-${index}`} checkpoint={checkpoint} index={index} />
        ))}
      </div>
    </section>
  );
}

/* ---------- 迁移任务 ---------- */

const TASK_KIND_LABEL: Record<string, string> = {
  retell: "复述",
  counter: "反驳",
  explain: "讲解",
  rewrite: "改写",
};

function TransferTaskSection({ task }: { task: DailyReaderTransferTask }) {
  return (
    <section className="mt-16">
      <SectionHeading zh="迁移任务" en="TRANSFER" />
      <div className="space-y-4">
        <span className={monoAccent}>{TASK_KIND_LABEL[task.taskKind] ?? task.taskKind}</span>
        <p className={zhBody}>{task.prompt}</p>
        {task.scaffold ? <p className={zhMeta}>{task.scaffold}</p> : null}
        {task.referencePoints.length > 0 ? (
          <div className="border-t border-[color:var(--dr-rule)] pt-3">
            <p className={monoLabel}>要点参照</p>
            <ul className="mt-2 space-y-1">
              {task.referencePoints.map((point, index) => (
                <li key={`rp-${index}`} className={`${zhMeta} flex gap-2`}>
                  <span aria-hidden="true">·</span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

/* ---------- Body ---------- */

export function DailyArticleBody({ article }: { article: DailyReaderArticle }) {
  const sentenceMapByUnit = new Map(article.sentenceMaps.map((map) => [map.unitId, map]));

  return (
    <>
      {/* 正文流 */}
      <div className="mt-4">
        {article.units.map((unit) => (
          <ReadingUnitView key={unit.id} unit={unit} sentenceMap={sentenceMapByUnit.get(unit.id)} />
        ))}
      </div>

      <StructureOutline article={article} />
      <LanguageTargetsSection article={article} />
      <CheckpointsSection article={article} />
      {article.transferTask ? <TransferTaskSection task={article.transferTask} /> : null}

      {/* 收束 */}
      {article.postReadSummary ? (
        <section className="mt-16 border-t border-[color:var(--dr-rule)] pt-10">
          <p className={monoLabel}>本篇收束</p>
          <p className={`${zhBody} mt-3`}>{article.postReadSummary}</p>
        </section>
      ) : null}
    </>
  );
}
