import { useState } from 'react';
import { Check, Copy, MessageSquarePlus, X } from 'lucide-react';
import { AnnotationCards } from '@/components/AnnotationCards';
import { JsonView } from '@/components/JsonView';
import { C, SPAN_TYPE_INFO } from '@/utils/colors';
import { fmtDuration, tryParseJson } from '@/utils/helpers';
import type { Annotation, Span } from '@/utils/types';

function CopyPayloadButton({ payload }: { payload: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      title="Copy payload"
      className="rounded p-0.5 transition hover:bg-white/10"
      style={{ color: copied ? C.green : C.fg0 }}
      onClick={() => {
        void navigator.clipboard.writeText(payload).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
    </button>
  );
}

function PayloadSection({ title, payload }: { title: string; payload: string }) {
  const parsed = tryParseJson(payload);
  const isStructured = parsed !== undefined && parsed !== null && typeof parsed === 'object';
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
          {title}
        </span>
        <CopyPayloadButton payload={payload} />
      </div>
      <div
        className="sb max-h-72 overflow-auto rounded p-2"
        style={{ background: C.subtle, border: `1px solid ${C.border}` }}
      >
        {isStructured ? (
          <JsonView data={parsed} />
        ) : (
          <pre
            className="num m-0 whitespace-pre-wrap break-words text-[11px]"
            style={{ color: C.fg2 }}
          >
            {payload}
          </pre>
        )}
      </div>
    </div>
  );
}

export function WireframeSpanDetail({
  span,
  annotations,
  onAnnotate,
  onClose,
}: {
  span: Span;
  annotations: Annotation[];
  onAnnotate: () => void;
  onClose: () => void;
}) {
  const info = SPAN_TYPE_INFO[span.span_type];
  const isErr = span.status === 'ERROR';
  const spanAnnotations = annotations.filter((a) => a.span_id === span.id);

  return (
    <div className="sb h-full space-y-3 overflow-auto p-3">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <span
            className="num rounded px-1.5 py-0.5 text-[10px] font-bold"
            style={{ color: info.color, background: `${info.color}15` }}
          >
            {info.label}
          </span>
          {isErr && (
            <span
              className="rounded px-1.5 py-0.5 text-[9px] font-bold"
              style={{ color: C.red, background: 'rgba(235,20,20,0.1)' }}
            >
              ERROR
            </span>
          )}
          <div className="flex-1" />
          <button
            className="inline-flex items-center rounded p-1 transition hover:bg-white/10"
            style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
            title="Annotate this span"
            aria-label="Annotate this span"
            onClick={onAnnotate}
          >
            <MessageSquarePlus className="size-3" />
          </button>
          <button
            className="inline-flex items-center rounded p-1 transition hover:bg-white/10"
            style={{ color: C.fg2, border: `1px solid ${C.border}` }}
            title="Close span details"
            aria-label="Close span details"
            onClick={onClose}
          >
            <X className="size-3" />
          </button>
        </div>
        <div className="num text-sm font-medium" style={{ color: C.fg4 }}>
          {span.name}
        </div>
      </div>

      <div className="num grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        <div style={{ color: C.fg0 }}>status</div>
        <div style={{ color: isErr ? C.red : C.fg2 }}>{span.status}</div>
        <div style={{ color: C.fg0 }}>duration</div>
        <div style={{ color: C.fg2 }}>{fmtDuration(span.duration_ms)}</div>
        {span.model && (
          <>
            <div style={{ color: C.fg0 }}>model</div>
            <div style={{ color: C.fg2 }}>{span.model}</div>
          </>
        )}
        {span.input_tokens != null && (
          <>
            <div style={{ color: C.fg0 }}>input tokens</div>
            <div style={{ color: C.fg2 }}>{span.input_tokens.toLocaleString()}</div>
          </>
        )}
        {span.output_tokens != null && (
          <>
            <div style={{ color: C.fg0 }}>output tokens</div>
            <div style={{ color: C.fg2 }}>{span.output_tokens.toLocaleString()}</div>
          </>
        )}
        <div style={{ color: C.fg0 }}>span id</div>
        <div style={{ color: C.fg0 }}>{span.id}</div>
      </div>

      {span.input_payload && <PayloadSection title="Input" payload={span.input_payload} />}
      {span.output_payload && <PayloadSection title="Output" payload={span.output_payload} />}

      {spanAnnotations.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Annotations
          </div>
          <AnnotationCards annotations={spanAnnotations} />
        </div>
      )}
    </div>
  );
}
