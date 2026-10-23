"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, rawFetch } from "./api";
import { useLiveEvents } from "./ws";

/** GET simples (JSON) com cache do React Query. */
export function useGet<T>(key: QueryKey, path: string | null, opts?: { refetchInterval?: number; keepPrevious?: boolean }) {
  return useQuery<T>({
    queryKey: key,
    queryFn: async () => (await rawFetch(path as string)).json() as Promise<T>,
    enabled: !!path,
    refetchInterval: opts?.refetchInterval,
    // ao trocar filtro, mostra a lista anterior até a nova chegar (sem piscar o esqueleto)
    placeholderData: opts?.keepPrevious ? keepPreviousData : undefined,
  });
}

type MutOpts<TBody> = { success?: string; invalidate?: QueryKey[]; requestBody?: (body: TBody) => unknown };

/** POST/PUT/PATCH/DELETE com toast e invalidação. */
export function useSend<TBody = unknown, TOut = unknown>(method: string, path: string | ((v: TBody) => string), opts: MutOpts<TBody> = {}) {
  const qc = useQueryClient();
  return useMutation<TOut, ApiError, TBody>({
    mutationFn: async (body: TBody) => {
      const url = typeof path === "function" ? path(body) : path;
      const requestBody = opts.requestBody ? opts.requestBody(body) : body;
      const res = await rawFetch(url, {
        method,
        headers: { "content-type": "application/json" },
        body: requestBody === undefined || method === "GET" ? undefined : JSON.stringify(requestBody),
      });
      const text = await res.text();
      return (text ? JSON.parse(text) : null) as TOut;
    },
    onSuccess: () => {
      if (opts.success) toast.success(opts.success);
      (opts.invalidate ?? []).forEach((k) => qc.invalidateQueries({ queryKey: k }));
    },
    onError: (e) => toast.error(e.message),
  });
}

/** Acompanha uma tarefa assíncrona (202 + task_id) até terminar. */
export function useTask(onDone?: (result: unknown) => void) {
  const qc = useQueryClient();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ pct: number; message: string } | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  const finished = useRef<string | null>(null); // WebSocket e polling podem chegar juntos: termina uma vez só
  const finish = (id: string, ok: boolean, result: unknown, error?: unknown) => {
    if (finished.current === id) return;
    finished.current = id;
    setProgress(null);
    setTaskId(null);
    if (ok) {
      toast.success("Concluído");
      qc.invalidateQueries();
      done.current?.(result);
    } else toast.error(`Falhou: ${String(error ?? "")}`.slice(0, 200));
  };
  const finishRef = useRef(finish);
  finishRef.current = finish;
  useLiveEvents((e) => {
    if (!taskId || e.data.task_id !== taskId) return;
    if (e.type === "task.progress") setProgress({ pct: Number(e.data.pct), message: String(e.data.message) });
    if (e.type === "task.done") finish(taskId, true, e.data.result);
    if (e.type === "task.failed") finish(taskId, false, null, e.data.error);
  });
  useEffect(() => {
    if (!taskId) return;
    const t = setInterval(async () => {
      try {
        const r = await (await rawFetch(`/api/v1/tasks/${taskId}`)).json();
        if (r.status === "done" || r.status === "failed") finishRef.current(taskId, r.status === "done", r.result, r.error);
        else setProgress({ pct: r.progress, message: r.message ?? "" });
      } catch {}
    }, 4000);
    return () => clearInterval(t);
  }, [taskId]);
  return {
    start: (id: string) => {
      setTaskId(id);
      setProgress({ pct: 0, message: "na fila" });
    },
    running: !!taskId,
    progress,
  };
}
