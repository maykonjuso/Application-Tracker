"use client";
/**
 * Componentes de interface (Tailwind + cva + Motion). Animações curtas e discretas; quem pede menos movimento
 * (prefers-reduced-motion) recebe a versão estática.
 */
import { cva, type VariantProps } from "class-variance-authority";
import { Check, ChevronDown, Inbox, X } from "lucide-react";
import { AnimatePresence, animate, motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import * as React from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export const EASE = [0.22, 1, 0.36, 1] as const;

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-all duration-150 disabled:pointer-events-none disabled:opacity-50 cursor-pointer whitespace-nowrap active:scale-[0.97] select-none",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-sm shadow-primary/25 hover:bg-primary-strong hover:shadow-md hover:shadow-primary/30",
        outline: "border bg-card hover:bg-muted hover:border-primary/40",
        ghost: "hover:bg-muted",
        soft: "bg-accent text-primary hover:bg-primary/15",
        danger: "bg-danger text-white hover:opacity-90",
        success: "bg-success text-white hover:opacity-90",
      },
      size: { sm: "h-8 px-3 text-xs", md: "h-9 px-4", lg: "h-11 px-6 text-[15px]", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export function Button({
  className,
  variant,
  size,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export function Card({ className, interactive, ...props }: React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean }) {
  return (
    <div
      className={cn(
        "rounded-xl border bg-card p-4 shadow-[var(--shadow-soft)]",
        interactive && "transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[var(--shadow-lift)]",
        className,
      )}
      {...props}
    />
  );
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("mb-3 text-sm font-semibold tracking-tight", className)} {...props} />;
}

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium leading-4", {
  variants: {
    tone: {
      neutral: "bg-muted text-muted-foreground",
      primary: "bg-accent text-primary",
      success: "bg-success/12 text-success",
      warning: "bg-warning/12 text-warning",
      danger: "bg-danger/12 text-danger",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({
  className,
  tone,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

/** Chip selecionável (filtros). */
export function Chip({
  active,
  count,
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; count?: number }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-all duration-150 active:scale-95",
        active
          ? "border-primary bg-primary text-primary-foreground shadow-sm shadow-primary/25"
          : "bg-card text-foreground hover:border-primary/50 hover:bg-accent",
        className,
      )}
      {...props}
    >
      {children}
      {count != null && <span className={cn("tabular-nums", active ? "opacity-80" : "text-muted-foreground")}>{count}</span>}
    </button>
  );
}

const fieldBase =
  "w-full rounded-lg border bg-card px-3 py-2 text-sm transition-colors placeholder:text-muted-foreground hover:border-primary/30 focus:border-primary focus:outline-none focus:ring-3 focus:ring-primary/15 disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(fieldBase, "h-9", className)} {...props} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(fieldBase, className)} {...props} />,
);
Textarea.displayName = "Textarea";

type Opt = { value: string; label: string; disabled?: boolean };

function textOf(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

function collectOptions(children: React.ReactNode): Opt[] {
  const out: Opt[] = [];
  React.Children.forEach(children, (child) => {
    if (!React.isValidElement<{ value?: string | number; children?: React.ReactNode; disabled?: boolean }>(child)) return;
    if (child.type === React.Fragment) {
      out.push(...collectOptions(child.props.children));
      return;
    }
    const label = textOf(child.props.children);
    out.push({ value: String(child.props.value ?? label), label, disabled: child.props.disabled });
  });
  return out;
}

const fold = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

type SelectProps = {
  value?: string | number;
  defaultValue?: string | number;
  onChange?: (e: React.ChangeEvent<HTMLSelectElement>) => void;
  children?: React.ReactNode;
  className?: string;
  disabled?: boolean;
  id?: string;
  "aria-label"?: string;
  placeholder?: string;
};

/**
 * Seleção com busca: clique e já digite para filtrar (sem acento/caixa), setas + Enter para escolher, Esc fecha.
 * Mesma API do <select> nativo (filhos <option> e onChange com e.target.value), então troca em todas as telas.
 */
export function Select({ value, defaultValue, onChange, children, className, disabled, id, placeholder, ...rest }: SelectProps) {
  const options = collectOptions(children);
  const [inner, setInner] = React.useState(String(defaultValue ?? options[0]?.value ?? ""));
  const current = value != null ? String(value) : inner;
  const selected = options.find((o) => o.value === current);
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const [rect, setRect] = React.useState<DOMRect | null>(null);
  const wrap = React.useRef<HTMLDivElement>(null);
  const input = React.useRef<HTMLInputElement>(null);
  const list = React.useRef<HTMLUListElement>(null);
  const listId = React.useId();
  const filtered = query ? options.filter((o) => fold(o.label).includes(fold(query))) : options;

  const place = React.useCallback(() => wrap.current && setRect(wrap.current.getBoundingClientRect()), []);
  const show = () => {
    if (disabled) return;
    place();
    setQuery("");
    setActive(Math.max(0, options.findIndex((o) => o.value === current)));
    setOpen(true);
  };
  const choose = (o: Opt) => {
    if (o.disabled) return;
    if (value == null) setInner(o.value);
    onChange?.({ target: { value: o.value }, currentTarget: { value: o.value } } as unknown as React.ChangeEvent<HTMLSelectElement>);
    setOpen(false);
    setQuery("");
    input.current?.blur();
  };

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!wrap.current?.contains(t) && !list.current?.contains(t)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  React.useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const below = rect ? window.innerHeight - rect.bottom > 260 || rect.top < 260 : true;
  return (
    <div ref={wrap} className={cn("relative w-full min-w-32", className)}>
      <input
        ref={input}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && filtered[active] ? `${listId}-${active}` : undefined}
        aria-label={rest["aria-label"]}
        disabled={disabled}
        value={open ? query : (selected?.label ?? "")}
        placeholder={open ? (selected?.label || "Digite para buscar…") : (placeholder ?? "Selecione…")}
        onClick={() => !open && show()}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          if (!open) show();
        }}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
            e.preventDefault();
            show();
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(filtered.length - 1, a + 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(0, a - 1));
          } else if (e.key === "Enter" && open) {
            e.preventDefault();
            if (filtered[active]) choose(filtered[active]);
          } else if (e.key === "Escape" && open) {
            // fecha só a lista, não o modal/gaveta em volta
            e.preventDefault();
            e.stopPropagation();
            setOpen(false);
          } else if (e.key === "Tab") {
            setOpen(false);
          }
        }}
        className={cn(fieldBase, "h-[inherit] min-h-9 cursor-pointer pr-9 text-left", open && "cursor-text")}
      />
      <ChevronDown
        size={16}
        aria-hidden
        className={cn("pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-transform", open && "rotate-180")}
      />
      {open &&
        rect &&
        createPortal(
          <ul
            ref={list}
            id={listId}
            role="listbox"
            className="fixed z-[70] max-h-64 overflow-y-auto rounded-xl border bg-card p-1 shadow-[var(--shadow-lift)]"
            style={{
              left: Math.max(8, Math.min(rect.left, window.innerWidth - Math.max(rect.width, 200) - 8)),
              width: Math.min(Math.max(rect.width, 200), window.innerWidth - 16),
              ...(below ? { top: rect.bottom + 4 } : { bottom: window.innerHeight - rect.top + 4 }),
            }}
          >
            {filtered.length === 0 && <li className="px-3 py-2 text-sm text-muted-foreground">Nada encontrado</li>}
            {filtered.map((o, idx) => (
              <li
                key={`${o.value}-${idx}`}
                id={`${listId}-${idx}`}
                data-idx={idx}
                role="option"
                aria-selected={o.value === current}
                aria-disabled={o.disabled}
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(o);
                }}
                onMouseEnter={() => setActive(idx)}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm",
                  idx === active && "bg-accent text-primary",
                  o.disabled && "cursor-not-allowed opacity-50",
                )}
              >
                <span className="truncate">{o.label || "—"}</span>
                {o.value === current && <Check size={15} className="shrink-0 text-primary" />}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-xs font-medium text-muted-foreground", className)} {...props} />;
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "animate-shimmer rounded-xl bg-[length:200%_100%] bg-gradient-to-r from-muted via-card to-muted",
        className,
      )}
    />
  );
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div aria-hidden="true" className={cn("space-y-2", className)}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className={cn("h-3", index === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export function SkeletonCard({ className, lines = 2 }: { className?: string; lines?: number }) {
  return (
    <Card aria-hidden="true" className={cn("space-y-3", className)}>
      <div className="flex items-center gap-3">
        <Skeleton className="h-10 w-10 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-2/5" />
          <Skeleton className="h-3 w-1/4" />
        </div>
      </div>
      <SkeletonText lines={lines} />
      <div className="flex gap-2">
        <Skeleton className="h-7 w-20 rounded-lg" />
        <Skeleton className="h-7 w-16 rounded-lg" />
      </div>
    </Card>
  );
}

export function SkeletonList({
  count = 3,
  className,
  cardClassName,
  lines = 2,
}: {
  count?: number;
  className?: string;
  cardClassName?: string;
  lines?: number;
}) {
  return (
    <div role="status" aria-label="Carregando conteúdo" className={cn("grid gap-3", className)}>
      <span className="sr-only">Carregando conteúdo</span>
      {Array.from({ length: count }, (_, index) => <SkeletonCard key={index} className={cardClassName} lines={lines} />)}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  icon,
  media,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  icon?: React.ReactNode;
  /** Elemento no lugar do ícone (ex.: logo da empresa), sem o fundo colorido. */
  media?: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: EASE }}
      className="mb-5 flex flex-wrap items-end justify-between gap-3"
    >
      <div className="flex items-center gap-3">
        {media}
        {!media && icon && (
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-primary to-sky-500 text-white shadow-md shadow-primary/25">
            {icon}
          </div>
        )}
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </motion.div>
  );
}

export function EmptyState({
  title,
  children,
  icon,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3, ease: EASE }}
      className="flex flex-col items-center rounded-xl border border-dashed bg-card/50 px-6 py-12 text-center"
    >
      <div className="mb-3 grid h-12 w-12 place-items-center rounded-full bg-accent text-primary">
        {icon ?? <Inbox size={22} />}
      </div>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1.5 max-w-md text-sm text-muted-foreground">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </motion.div>
  );
}

/** Número que "conta" até o valor (para KPIs). */
export function AnimatedNumber({ value, format }: { value: number; format?: (n: number) => string }) {
  const ref = React.useRef<HTMLSpanElement>(null);
  const reduce = useReducedMotion();
  const last = React.useRef(0);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fmt = format ?? ((n: number) => Math.round(n).toLocaleString("pt-BR"));
    if (reduce) {
      el.textContent = fmt(value);
      return;
    }
    const controls = animate(last.current, value, {
      duration: 0.8,
      ease: EASE,
      onUpdate: (v) => (el.textContent = fmt(v)),
    });
    last.current = value;
    return () => controls.stop();
  }, [value, format, reduce]);
  return <span ref={ref} className="tabular-nums" />;
}

export function Stat({
  label,
  value,
  hint,
  icon,
  tone = "primary",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  icon?: React.ReactNode;
  tone?: "primary" | "success" | "warning" | "danger";
}) {
  const tones = {
    primary: "bg-accent text-primary",
    success: "bg-success/12 text-success",
    warning: "bg-warning/12 text-warning",
    danger: "bg-danger/12 text-danger",
  };
  return (
    <Card interactive className="flex items-start justify-between gap-3">
      <div>
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tracking-tight">
          {typeof value === "number" ? <AnimatedNumber value={value} /> : value}
        </p>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      {icon && <div className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", tones[tone])}>{icon}</div>}
    </Card>
  );
}

function scoreTone(v: number) {
  return v >= 60 ? "success" : v >= 40 ? "warning" : "danger";
}

export function ScoreBar({ value, label }: { value: number; label: string }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  const tone = { success: "bg-success", warning: "bg-warning", danger: "bg-danger" }[scoreTone(v)];
  return (
    <div className="flex items-center gap-2 text-xs" aria-label={`${label} ${v}`}>
      <span className="w-24 text-muted-foreground">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <motion.div
          className={cn("h-full rounded-full", tone)}
          initial={{ width: 0 }}
          animate={{ width: `${v}%` }}
          transition={{ duration: 0.7, ease: EASE }}
        />
      </div>
      <span className="w-7 text-right font-semibold tabular-nums">{v}</span>
    </div>
  );
}

/** Anel de nota (0–100) com animação de preenchimento. */
export function ScoreRing({ value, label, size = 46 }: { value: number; label: string; size?: number }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  const color = { success: "var(--success)", warning: "var(--warning)", danger: "var(--danger)" }[scoreTone(v)];
  return (
    <div className="flex flex-col items-center gap-0.5" aria-label={`${label} ${v} de 100`} title={`${label}: ${v}`}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--muted)" strokeWidth={5} />
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray={c}
            initial={{ strokeDashoffset: c }}
            animate={{ strokeDashoffset: c * (1 - v / 100) }}
            transition={{ duration: 0.9, ease: EASE }}
          />
        </svg>
        <span className="absolute inset-0 grid place-items-center text-xs font-semibold tabular-nums">{v}</span>
      </div>
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
    </div>
  );
}

const AVATAR_COLORS = [
  "from-blue-600 to-sky-500",
  "from-sky-500 to-cyan-500",
  "from-emerald-500 to-teal-500",
  "from-amber-500 to-orange-500",
  "from-rose-500 to-pink-500",
  "from-cyan-500 to-blue-600",
];

/** Iniciais da empresa/pessoa com cor estável. */
export function Avatar({ name, className }: { name: string | null | undefined; className?: string }) {
  const n = (name ?? "?").trim();
  const initials = n
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
  let h = 0;
  for (const ch of n) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return (
    <div
      aria-hidden
      className={cn(
        "grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-sm font-semibold text-white shadow-sm",
        AVATAR_COLORS[h % AVATAR_COLORS.length],
        className,
      )}
    >
      {initials || "?"}
    </div>
  );
}

/** Lista com entrada escalonada dos itens. */
export function Stagger({ className, children, ...props }: HTMLMotionProps<"div">) {
  return (
    <motion.div
      className={className}
      initial="hidden"
      animate="show"
      variants={{ hidden: {}, show: { transition: { staggerChildren: 0.04 } } }}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ className, children, ...props }: HTMLMotionProps<"div">) {
  return (
    <motion.div
      className={className}
      variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: EASE } } }}
      {...props}
    >
      {children}
    </motion.div>
  );
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Diálogos acessíveis: foco entra no diálogo, Tab fica preso nele, Esc fecha, o foco volta ao botão que abriu e a página atrás não rola. */
export function useDialog(ref: React.RefObject<HTMLElement | null>, open: boolean, onClose: () => void) {
  const closeRef = React.useRef(onClose);
  closeRef.current = onClose;
  React.useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = window.setTimeout(() => {
      const el = ref.current;
      const first = el?.querySelector<HTMLElement>("[data-autofocus]") ?? el?.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? el)?.focus({ preventScroll: true });
    }, 30);
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab" || !ref.current) return;
      const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((x) => x.offsetParent !== null);
      if (!items.length) return;
      const first = items[0]!, last = items[items.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      } else if (!ref.current.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      if (before?.isConnected) before.focus({ preventScroll: true });
    };
  }, [open, ref]);
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const box = React.useRef<HTMLDivElement>(null);
  useDialog(box, open, onClose);
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm"
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
        >
          <motion.div
            ref={box}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className={cn(
              "max-h-[90vh] w-full overflow-auto rounded-2xl border bg-card p-6 shadow-2xl",
              wide ? "max-w-4xl" : "max-w-lg",
            )}
            onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
              <button
                aria-label="Fechar"
                onClick={onClose}
                className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X size={16} />
              </button>
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: string; count?: number }[];
}) {
  const id = React.useId();
  return (
    <div className="mb-5 inline-flex flex-wrap gap-1 rounded-xl border bg-card p-1 shadow-[var(--shadow-soft)]" role="tablist">
      {items.map((it) => {
        const active = value === it.value;
        return (
          <button
            key={it.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(it.value)}
            className={cn(
              "relative rounded-lg px-3 py-1.5 text-sm transition-colors",
              active ? "font-medium text-primary-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {active && (
              <motion.span
                layoutId={`tab-${id}`}
                className="absolute inset-0 rounded-lg bg-primary shadow-sm shadow-primary/30"
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            )}
            <span className="relative">
              {it.label}
              {it.count != null && <span className="ml-1 text-xs opacity-75">({it.count})</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}
