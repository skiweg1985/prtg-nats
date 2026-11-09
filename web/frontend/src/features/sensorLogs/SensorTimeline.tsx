import { useTranslation } from "react-i18next";
import clsx from "clsx";

import type {
  SensorTimeline,
  SensorTimelineBucket,
  SensorTimelineState,
  SensorTimelineStream,
} from "@/api/types";
import { formatSpanEnd } from "@/utils/format";

/**
 * A sensor's runs per network as bars on one shared time axis.
 *
 * Meant to answer "is it fine?" at a glance, so the colours carry the
 * weight: a single failed run between passing ones stays a quiet mark on a
 * green slice, and only a failure that lasted is red. Networks sit on the
 * same axis so an outage that hit all of them at once lines up.
 */

const STATE_CLASS: Record<SensorTimelineState, string> = {
  ok: "bg-ok/70",
  outlier: "bg-ok/70",
  failing: "bg-danger",
  none: "bg-surface-2",
};

// How often the axis is labelled, in hours of local time.
const AXIS_STEP_HOURS = 6;

export function SensorTimelineRows({ timeline }: { timeline: SensorTimeline }) {
  const { t } = useTranslation();
  const signIns = timeline.sensor === "wlan-auth";
  const buckets = timeline.streams[0]?.buckets ?? [];
  return (
    <div className="space-y-3">
      {/* Every bar spans the full width, so all of them share one axis
          whatever the figures above them need. */}
      {timeline.streams.map((stream) => (
        <TimelineRow
          key={stream.ssid ?? ""}
          stream={stream}
          label={stream.ssid ?? timeline.sensor}
          signIns={signIns}
        />
      ))}
      <Axis buckets={buckets} />
      <ul className="text-ink-3 flex flex-wrap gap-x-4 gap-y-1 pt-1 text-xs">
        {(["ok", "outlier", "failing", "none"] as const).map((state) => (
          <li key={state} className="flex items-center gap-1.5">
            <Slice state={state} className="h-3 w-2 rounded-[2px]" />
            {t(`probeSensor.legend.${state}`)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TimelineRow({
  stream,
  label,
  signIns,
}: {
  stream: SensorTimelineStream;
  label: string;
  signIns: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
        <span
          className="text-ink truncate font-mono text-xs font-medium"
          title={label}
        >
          {label}
        </span>
        <Figures stream={stream} signIns={signIns} />
      </div>
      <div
        role="img"
        aria-label={t("probeSensor.rowLabel", {
          network: label,
          ok: stream.ok,
          error: stream.error,
        })}
        className="flex h-6 overflow-hidden rounded-[3px]"
      >
        {stream.buckets.map((bucket) => (
          <Slice key={bucket.start} state={bucket.state} className="flex-1" />
        ))}
      </div>
    </div>
  );
}

function Figures({
  stream,
  signIns,
}: {
  stream: SensorTimelineStream;
  signIns: boolean;
}) {
  const { t, i18n } = useTranslation();
  const percent = new Intl.NumberFormat(i18n.resolvedLanguage, {
    style: "percent",
    maximumFractionDigits: 1,
  });
  const failure = stream.last_failure;
  return (
    <dl className="text-ink-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs tabular-nums">
      <div className="flex gap-1 ">
        <dt className="sr-only">{t("probeSensor.successRate")}</dt>
        <dd className="text-ink font-medium">
          {stream.success_rate === null
            ? "—"
            : percent.format(stream.success_rate)}
        </dd>
      </div>
      <div className="flex gap-1 ">
        <dt className="sr-only">
          {t(
            signIns ? "probeSensor.signInsPerHour" : "probeSensor.runsPerHour",
          )}
        </dt>
        <dd>
          {stream.runs_per_hour === null
            ? "—"
            : t(
                signIns
                  ? "probeSensor.perHourSignIns"
                  : "probeSensor.perHourRuns",
                {
                  count: Math.round(stream.runs_per_hour),
                },
              )}
        </dd>
      </div>
      <div className="flex gap-1">
        <dt className="sr-only">{t("probeSensor.lastFailure")}</dt>
        <dd className={failure ? "text-danger" : "text-ink-3"}>
          {failure
            ? t("probeSensor.lastFailureAt", {
                time: formatSpanEnd(new Date().toISOString(), failure.time),
                code: failure.code ?? failure.summary,
              })
            : t("probeSensor.noFailure")}
        </dd>
      </div>
    </dl>
  );
}

function Slice({
  state,
  className,
}: {
  state: SensorTimelineState;
  className?: string;
}) {
  return (
    <span className={clsx("relative block", STATE_CLASS[state], className)}>
      {state === "outlier" && (
        <span className="bg-danger absolute inset-x-0 bottom-0 h-1/3" />
      )}
    </span>
  );
}

/** Hour labels under the bars, at the slices where a labelled hour starts. */
function Axis({ buckets }: { buckets: SensorTimelineBucket[] }) {
  const { i18n } = useTranslation();
  if (buckets.length === 0) return null;
  const hour = new Intl.DateTimeFormat(i18n.resolvedLanguage, {
    hour: "2-digit",
    minute: "2-digit",
  });
  const marks = buckets.flatMap((bucket, index) => {
    const when = new Date(bucket.start);
    return when.getMinutes() === 0 && when.getHours() % AXIS_STEP_HOURS === 0
      ? [{ index, label: hour.format(when) }]
      : [];
  });
  return (
    <div className="text-ink-3 relative h-4 text-[11px] tabular-nums">
      {marks.map((mark) => (
        <span
          key={mark.index}
          className="absolute -translate-x-1/2"
          style={{ left: `${(mark.index / buckets.length) * 100}%` }}
        >
          {mark.label}
        </span>
      ))}
    </div>
  );
}
