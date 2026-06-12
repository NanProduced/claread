"use client";

import { useEffect, useLayoutEffect, useRef, useState, FormEvent } from "react";
import { motion } from "motion/react";
import type { Engine, Runner, World, Body, MouseConstraint as MC, Mouse as MatterMouse } from "matter-js";

const useIsomorphicLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

// ─── Tuning ──────────────────────────────────────────────────────────────────
const GRAVITY_SCALE = 0.0012;
const RESTITUTION   = 0.05;
const FRICTION      = 0.6;
const FRICTION_AIR  = 0.02;
const DENSITY       = 0.0015;
const STICKER_CAP   = 60;
const FADE_MS       = 250;

const WALL_THICKNESS = 60;

const TEXT_FONT_PX   = 14;
const TEXT_MAX_WIDTH = 220; // Slightly wider for sleek pill look
const TEXT_PAD_X     = 18;
const TEXT_PAD_Y     = 12;
const TEXT_LINE_H    = 22;

const EMOJI_SIZE     = 64;
const EMOJI_FONT_PX  = 34;

// ─── Sticker Color Mapping ───────────────────────────────────────────────────
// Maps vibrant sticker colors to their dark borders for high-contrast die-cut styling
const STICKER_COLORS: Record<string, { fill: string; border: string }> = {
  orange: { fill: "#F59E0B", border: "#B45309" }, // Warm Gold
  green:  { fill: "#10B981", border: "#047857" }, // Emerald
  pink:   { fill: "#EC4899", border: "#BE185D" }, // Hot Pink
  indigo: { fill: "#6366F1", border: "#4338CA" }, // Indigo
  blue:   { fill: "#3B82F6", border: "#1D4ED8" }, // Sky Blue
  violet: { fill: "#8B5CF6", border: "#6D28D9" }, // Violet
};

const BG_DARK  = "#111115"; // Footer background match

const SEED_QUOTES = [
  "不再痛苦地反复查词典了！🙌",
  "双语对照功能真的好克制，爱了 ❤️",
  "长难句结构拆解太清晰了！🔥",
  "AI chat 能随时提问，很有安全感 💬",
  "配合生词本，看英文原著效率翻倍 📈",
  "每日精读推荐的文章质量很高 💎",
  "行间旁注设计不会打断阅读节奏 🎯",
  "精读的时候没有杂乱无关的AI回答 🔕",
  "词汇分类很棒：语境义/习惯搭配/术语 📑",
  "这是我用过最优雅的精读工具 ⭐",
];

const SEED_EMOJIS = ["👏", "💡", "🙌", "👀", "💬", "✅", "🔥", "💯", "🎉", "❤️", "🤔", "⭐"];

// ─── Types ───────────────────────────────────────────────────────────────────
type StickerKind = "text" | "emoji";

interface Sticker {
  body: Body;
  kind: StickerKind;
  content: string;
  w: number;
  h: number;
  colorKey: string; // Key in STICKER_COLORS
  lines: string[]; // pre-wrapped text lines; empty for emoji
  createdAt: number;
  fadeStart?: number;
}

type BodyWithPlugin = Body & { plugin: { sticker?: Sticker } };

// ─── Helpers ─────────────────────────────────────────────────────────────────
function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

// Word-wrap text into lines that fit within maxWidth (CJK + English mixed)
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  let current = "";
  
  const tokens = text.match(/[\u4e00-\u9fa5]|[a-zA-Z0-9'\s\p{Emoji}]+/gu) || [text];
  
  for (const token of tokens) {
    const isEnOrSpace = /^[a-zA-Z0-9'\s]+$/.test(token);
    const candidate = current 
      ? (isEnOrSpace && /^[a-zA-Z0-9']+$/.test(current.slice(-1)) ? `${current} ${token.trim()}` : `${current}${token}`)
      : token;
    
    const width = ctx.measureText(candidate).width;
    if (width <= maxWidth) {
      current = candidate;
    } else if (!current) {
      lines.push(token);
      current = "";
    } else {
      lines.push(current);
      current = token;
    }
  }
  if (current) lines.push(current);
  return lines.length > 0 ? lines : [""];
}

function measureTextCard(
  ctx: CanvasRenderingContext2D,
  text: string,
): { lines: string[]; w: number; h: number } {
  ctx.save();
  ctx.font = `800 ${TEXT_FONT_PX}px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
  const lines = wrapText(ctx, text, TEXT_MAX_WIDTH);
  let maxW = 0;
  for (const line of lines) {
    const lw = ctx.measureText(line).width;
    if (lw > maxW) maxW = lw;
  }
  ctx.restore();
  const w = Math.max(90, Math.round(maxW + TEXT_PAD_X * 2));
  const h = Math.max(46, Math.round(lines.length * TEXT_LINE_H + TEXT_PAD_Y * 2));
  return { lines, w, h };
}

function randBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

// ─── Component ───────────────────────────────────────────────────────────────
export function ProductStickerWall() {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef    = useRef<HTMLCanvasElement>(null);
  const inputRef     = useRef<HTMLInputElement>(null);

  // Handles for submit-from-outside-effect access.
  const engineRef    = useRef<Engine | null>(null);
  const worldRef     = useRef<World | null>(null);
  const stickersRef  = useRef<Sticker[]>([]);
  const sizeRef      = useRef<{ w: number; h: number }>({ w: 0, h: 0 });
  const measureCtxRef = useRef<CanvasRenderingContext2D | null>(null);
  const matterRef    = useRef<typeof import("matter-js") | null>(null);

  // ── Physics + render loop ─────────────────────────────────────────────────
  useEffect(() => {
    const canvas    = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    measureCtxRef.current = ctx;

    let alive = true;
    let rafId = 0;

    let engine: Engine | null = null;
    let runner: Runner | null = null;
    let world: World | null = null;
    let walls: Body[] = [];
    let mouse: MatterMouse | null = null;
    let mouseConstraint: MC | null = null;
    let ro: ResizeObserver | null = null;

    let dpr = Math.min(window.devicePixelRatio || 1, 2);

    function buildWalls(Matter: typeof import("matter-js"), w: number, h: number): Body[] {
      const t = WALL_THICKNESS;
      const opts = { isStatic: true, render: { visible: false } };
      return [
        Matter.Bodies.rectangle(w / 2, -t / 2, w + t * 2, t, opts), // top
        Matter.Bodies.rectangle(w / 2, h + t / 2, w + t * 2, t, opts), // bottom
        Matter.Bodies.rectangle(-t / 2, h / 2, t, h + t * 2, opts), // left
        Matter.Bodies.rectangle(w + t / 2, h / 2, t, h + t * 2, opts), // right
      ];
    }

    function makeTextSticker(
      Matter: typeof import("matter-js"),
      text: string,
      x: number,
      y: number,
      colorKey: string,
      spawnMotion: boolean,
    ): Sticker {
      const { lines, w, h } = measureTextCard(ctx!, text);
      const body = Matter.Bodies.rectangle(x, y, w, h, {
        restitution: RESTITUTION,
        friction: FRICTION,
        frictionAir: FRICTION_AIR,
        density: DENSITY,
        angle: randBetween(-0.25, 0.25),
        render: { visible: false },
      });
      if (spawnMotion) {
        Matter.Body.setAngularVelocity(body, randBetween(-0.03, 0.03));
        Matter.Body.setVelocity(body, { x: randBetween(-0.3, 0.3), y: 0 });
      }
      const sticker: Sticker = {
        body,
        kind: "text",
        content: text,
        w,
        h,
        colorKey,
        lines,
        createdAt: performance.now(),
      };
      ;(body as BodyWithPlugin).plugin = { sticker };
      return sticker;
    }

    function makeEmojiSticker(
      Matter: typeof import("matter-js"),
      emoji: string,
      x: number,
      y: number,
      colorKey: string,
    ): Sticker {
      const body = Matter.Bodies.rectangle(x, y, EMOJI_SIZE, EMOJI_SIZE, {
        restitution: RESTITUTION,
        friction: FRICTION,
        frictionAir: FRICTION_AIR,
        density: DENSITY,
        angle: randBetween(-0.25, 0.25),
        render: { visible: false },
      });
      const sticker: Sticker = {
        body,
        kind: "emoji",
        content: emoji,
        w: EMOJI_SIZE,
        h: EMOJI_SIZE,
        colorKey,
        lines: [],
        createdAt: performance.now(),
      };
      ;(body as BodyWithPlugin).plugin = { sticker };
      return sticker;
    }

    function seed(Matter: typeof import("matter-js"), w: number, h: number) {
      const palette = Object.keys(STICKER_COLORS);
      
      // Limit to 5 text stickers and 5 emojis initially to keep the canvas clean and allow space
      const textSeeds = SEED_QUOTES.slice(0, 5);
      const emojiSeeds = SEED_EMOJIS.slice(0, 5);

      // Seed text stickers in the upper half so they fall down
      textSeeds.forEach((quote, i) => {
        const colorKey = palette[i % palette.length];
        const x = randBetween(120, Math.max(150, w - 120));
        const y = randBetween(150, h / 2 - 30);
        const sticker = makeTextSticker(Matter, quote, x, y, colorKey, false);
        Matter.Body.setAngularVelocity(sticker.body, randBetween(-0.03, 0.03));
        Matter.Body.setVelocity(sticker.body, { x: randBetween(-0.3, 0.3), y: randBetween(-0.3, 0.3) });
        Matter.Composite.add(world!, sticker.body);
        stickersRef.current.push(sticker);
      });

      // Seed emoji stickers
      emojiSeeds.forEach((emoji, i) => {
        const colorKey = palette[(i + 2) % palette.length];
        const x = randBetween(80, Math.max(100, w - 80));
        const y = randBetween(150, h / 2 - 30);
        const sticker = makeEmojiSticker(Matter, emoji, x, y, colorKey);
        Matter.Body.setAngularVelocity(sticker.body, randBetween(-0.03, 0.03));
        Matter.Body.setVelocity(sticker.body, { x: randBetween(-0.3, 0.3), y: randBetween(-0.3, 0.3) });
        Matter.Composite.add(world!, sticker.body);
        stickersRef.current.push(sticker);
      });
    }

    function resize() {
      const Matter = matterRef.current;
      if (!Matter || !world) return;
      const w = container!.clientWidth || 480;
      const h = container!.clientHeight || 480;
      dpr = Math.min(window.devicePixelRatio || 1, 2);

      canvas!.width  = Math.round(w * dpr);
      canvas!.height = Math.round(h * dpr);
      canvas!.style.width  = `${w}px`;
      canvas!.style.height = `${h}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      if (walls.length > 0) {
        for (const wall of walls) Matter.Composite.remove(world, wall);
      }
      walls = buildWalls(Matter, w, h);
      Matter.Composite.add(world, walls);

      for (const s of stickersRef.current) {
        const p = s.body.position;
        let nx = p.x;
        let ny = p.y;
        if (nx < 30) nx = 30;
        if (nx > w - 30) nx = w - 30;
        if (ny > h - 30) ny = h - 30;
        if (nx !== p.x || ny !== p.y) Matter.Body.setPosition(s.body, { x: nx, y: ny });
      }

      if (mouse) mouse.pixelRatio = dpr;
      sizeRef.current = { w, h };
    }

    function drawFrame(now: number) {
      if (!alive) return;
      const bg = BG_DARK;
      const { w: W, h: H } = sizeRef.current;

      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx!.fillStyle = bg;
      ctx!.fillRect(0, 0, W, H);

      const stickers = stickersRef.current;
      const Matter = matterRef.current;

      if (Matter && world) {
        for (let i = stickers.length - 1; i >= 0; i--) {
          const s = stickers[i];
          if (s.fadeStart !== undefined) {
            const dt = now - s.fadeStart;
            if (dt >= FADE_MS) {
              Matter.Composite.remove(world, s.body);
              stickers.splice(i, 1);
            }
          }
        }
      }

      for (const s of stickers) {
        const { body, w, h, colorKey, kind, lines, content } = s;
        const colorInfo = STICKER_COLORS[colorKey] || STICKER_COLORS.blue;

        let alpha = 1;
        if (s.fadeStart !== undefined) {
          const dt = now - s.fadeStart;
          alpha = Math.max(0, 1 - dt / FADE_MS);
        }

        ctx!.save();
        ctx!.globalAlpha = alpha;
        ctx!.translate(body.position.x, body.position.y);
        ctx!.rotate(body.angle);

        // Capsule corner radius for text, perfect circle radius for emojis
        const radius = kind === "emoji" ? w / 2 : h / 2;

        // 1. Draw outer colored outline with a physical drop shadow
        ctx!.shadowColor = "rgba(0, 0, 0, 0.3)";
        ctx!.shadowBlur = 10;
        ctx!.shadowOffsetX = 0;
        ctx!.shadowOffsetY = 5;

        ctx!.strokeStyle = colorInfo.border;
        ctx!.lineWidth = 6; // Outer thick colored border
        roundedRect(ctx!, -w / 2, -h / 2, w, h, radius);
        ctx!.stroke();

        // Disable shadow for inner elements to keep rendering clean
        ctx!.shadowColor = "transparent";
        ctx!.shadowBlur = 0;
        ctx!.shadowOffsetX = 0;
        ctx!.shadowOffsetY = 0;

        // 2. Draw white border (sandwiched inside the outline)
        ctx!.strokeStyle = "#FFFFFF";
        ctx!.lineWidth = 4; // White die-cut border
        roundedRect(ctx!, -w / 2, -h / 2, w, h, radius);
        ctx!.stroke();

        // 3. Draw inner solid colored fill
        ctx!.fillStyle = colorInfo.fill;
        roundedRect(ctx!, -w / 2, -h / 2, w, h, radius);
        ctx!.fill();

        // 4. Draw text or emoji
        if (kind === "text") {
          ctx!.fillStyle = "#FFFFFF"; // Premium white text on solid color
          ctx!.font = `800 ${TEXT_FONT_PX}px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
          ctx!.textAlign = "center";
          ctx!.textBaseline = "middle";
          
          const totalH = lines.length * TEXT_LINE_H;
          const startY = -totalH / 2 + TEXT_LINE_H / 2;
          for (let li = 0; li < lines.length; li++) {
            ctx!.fillText(lines[li], 0, startY + li * TEXT_LINE_H);
          }
        } else {
          ctx!.font = `${EMOJI_FONT_PX}px ui-sans-serif, system-ui, -apple-system, Segoe UI, "Apple Color Emoji", "Segoe UI Emoji", sans-serif`;
          ctx!.textAlign = "center";
          ctx!.textBaseline = "middle";
          ctx!.fillText(content, 0, 1);
        }

        ctx!.restore();
      }

      rafId = requestAnimationFrame(drawFrame);
    }

    import("matter-js").then((Matter) => {
      if (!alive) return;
      matterRef.current = Matter;

      engine = Matter.Engine.create({ gravity: { x: 0, y: 1, scale: GRAVITY_SCALE } });
      engine.timing.timeScale = 0.6;
      world = engine.world;
      engineRef.current = engine;
      worldRef.current = world;

      runner = Matter.Runner.create();
      Matter.Runner.run(runner, engine);

      resize();
      mouse = Matter.Mouse.create(canvas!);
      mouse.pixelRatio = dpr;
      mouseConstraint = Matter.MouseConstraint.create(engine, {
        mouse,
        constraint: {
          stiffness: 0.2,
          damping: 0.1,
          render: { visible: false },
        },
      });
      Matter.Composite.add(world, mouseConstraint);

      seed(Matter, sizeRef.current.w, sizeRef.current.h);

      ro = new ResizeObserver(resize);
      ro.observe(container!);

      rafId = requestAnimationFrame(drawFrame);
    });

    return () => {
      alive = false;
      cancelAnimationFrame(rafId);
      if (ro) ro.disconnect();
      const Matter = matterRef.current;
      if (Matter) {
        if (runner) Matter.Runner.stop(runner);
        if (world) Matter.Composite.clear(world, false, true);
        if (engine) Matter.Engine.clear(engine);
      }
      matterRef.current = null;
      engineRef.current = null;
      worldRef.current = null;
      stickersRef.current = [];
      measureCtxRef.current = null;
    };
  }, []);

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const input = inputRef.current;
    if (!input) return;
    const value = input.value.trim();
    if (!value) return;

    const Matter = matterRef.current;
    const world = worldRef.current;
    const ctx = measureCtxRef.current;
    if (!Matter || !world || !ctx) return;

    const { w: W } = sizeRef.current;
    if (W === 0) return;

    const palette = Object.keys(STICKER_COLORS);
    const colorKey = palette[Math.floor(Math.random() * palette.length)];
    const x = randBetween(100, Math.max(120, W - 100));
    const y = -30;

    const { lines, w, h } = measureTextCard(ctx, value);
    const body = Matter.Bodies.rectangle(x, y, w, h, {
      restitution: RESTITUTION,
      friction: FRICTION,
      frictionAir: FRICTION_AIR,
      density: DENSITY,
      angle: randBetween(-0.25, 0.25),
      render: { visible: false },
    });
    Matter.Body.setAngularVelocity(body, randBetween(-0.03, 0.03));
    Matter.Body.setVelocity(body, { x: randBetween(-0.3, 0.3), y: 0 });

    const sticker: Sticker = {
      body,
      kind: "text",
      content: value,
      w,
      h,
      colorKey,
      lines,
      createdAt: performance.now(),
    };
    ;(body as BodyWithPlugin).plugin = { sticker };

    Matter.Composite.add(world, body);
    stickersRef.current.push(sticker);

    if (stickersRef.current.length > STICKER_CAP) {
      for (const s of stickersRef.current) {
        if (s.fadeStart === undefined) {
          s.fadeStart = performance.now();
          break;
        }
      }
    }

    input.value = "";
  }

  return (
    <motion.div
      ref={containerRef}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
      className="relative h-full w-full overflow-hidden rounded-2xl border border-white/5"
      style={{ background: BG_DARK, touchAction: "none" }}
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0"
        style={{ width: "100%", height: "100%", display: "block" }}
      />

      <style>{`
        .sticker-wall-input::placeholder { color: rgba(255,255,255,0.35); }
        .sticker-wall-pill {
          transition: background 180ms ease, border-color 180ms ease, transform 180ms ease, box-shadow 180ms ease;
        }
        .sticker-wall-pill:hover {
          background: rgba(255,255,255,0.08) !important;
          border-color: rgba(255,255,255,0.2) !important;
          transform: translateY(-1px);
        }
        .sticker-wall-pill:focus-within {
          background: rgba(255,255,255,0.12) !important;
          border-color: rgba(255,255,255,0.4) !important;
          transform: translateY(-1px) scale(1.01);
          box-shadow: 0 8px 20px rgba(0,0,0,0.4), 0 0 0 4px rgba(255,255,255,0.05) !important;
        }
        .sticker-wall-pill:active {
          transform: translateY(0) scale(0.99);
        }
        .sticker-wall-send {
          transition: transform 120ms ease, box-shadow 120ms ease, filter 120ms ease;
          cursor: pointer;
        }
        .sticker-wall-send:hover {
          transform: translateY(-1px);
          filter: brightness(1.1);
        }
        .sticker-wall-send:active {
          transform: translateY(1px);
          filter: brightness(0.9);
        }
      `}</style>

      {/* Position form at top-start to prevent overlapping with falling/piled stickers */}
      <form
        onSubmit={onSubmit}
        className="pointer-events-none absolute inset-0 flex flex-col items-center justify-start gap-4 px-4 pt-10 sm:pt-12"
      >
        <div className="pointer-events-none flex flex-col items-center gap-1.5 text-center">
          <h3
            className="select-none font-headline text-2xl font-bold tracking-tight text-white/95"
            style={{ textShadow: "0 2px 12px rgba(0,0,0,0.6)" }}
          >
            Claread 留言板
          </h3>
          <p className="select-none text-xs font-semibold text-white/45 max-w-sm">
            留下你的体验或心流印记。支持自由拖拽碰撞，物理仿真。
          </p>
        </div>
        
        <div
          className="sticker-wall-pill pointer-events-auto flex w-full max-w-sm items-center gap-2 rounded-full p-1 border border-white/10"
          style={{
            background: "rgba(255,255,255,0.05)",
            backdropFilter: "blur(6px)",
          }}
        >
          <input
            ref={inputRef}
            type="text"
            placeholder="写下你对 Claread 的想法…"
            maxLength={60}
            className="sticker-wall-input flex-1 bg-transparent px-4 py-1.5 text-xs text-white/90 outline-none"
            style={{ fontWeight: 600 }}
          />
          <button
            type="submit"
            className="sticker-wall-send flex items-center rounded-full px-4.5 py-1.5 text-xs text-white bg-lens-blue"
            style={{ fontWeight: 700 }}
          >
            发送
          </button>
        </div>
      </form>
    </motion.div>
  );
}
