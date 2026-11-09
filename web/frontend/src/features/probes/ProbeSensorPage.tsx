import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useProbe, useProbePrtgName, useSensorLogTimeline } from "@/api/hooks";
import { PermissionGate } from "@/app/providers";
import { ErrorDetails } from "@/components/ui/ErrorDetails";
import {
  BackLink,
  Badge,
  Card,
  DetailRow,
  EmptyState,
  Mono,
  Skeleton,
} from "@/components/ui/primitives";
import { SensorStatusBadge } from "@/components/ui/status";
import { probeName } from "@/features/probes/probeName";
import { SensorTimelineRows } from "@/features/sensorLogs/SensorTimeline";

/**
 * One sensor on one probe: how it is deployed and how its runs went.
 *
 * The probe page used to list the runs of every sensor below its sensor
 * table, where they read as if they belonged to the table. Here they belong
 * to the sensor they came from.
 */
export function ProbeSensorPage() {
  const { t } = useTranslation();
  const { probeId, sensorName } = useParams<{
    probeId: string;
    sensorName: string;
  }>();
  const { data, isLoading, error, refetch } = useProbe(probeId);
  const { data: prtgName } = useProbePrtgName(probeId);

  if (isLoading) return <Skeleton className="h-64" />;
  if (error)
    return <ErrorDetails error={error} onRetry={() => void refetch()} />;
  if (!data) return null;

  const probe = probeName(
    data.summary,
    prtgName?.state === "ok" ? prtgName.name_in_prtg : null,
  );
  const sensor = data.sensors.find((entry) => entry.name === sensorName);
  const account = data.summary.nats_username;

  return (
    <div className="space-y-4">
      <header>
        <BackLink to={`/probes/${probeId}?tab=sensors`}>{probe}</BackLink>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <h1 className="text-lg">{sensorName}</h1>
          {sensor && <SensorStatusBadge status={sensor.status} />}
        </div>
        <p className="text-ink-2 text-sm">
          {t("probeSensor.onProbe", { probe })}
        </p>
      </header>

      {sensor ? (
        <Card title={t("probeSensor.deployment")}>
          <dl>
            <DetailRow label={t("probeSensor.version")}>
              <Mono>
                {t("probeSensor.versions", {
                  installed: sensor.installed_version ?? "—",
                  desired: sensor.desired_version ?? "—",
                })}
              </Mono>
            </DetailRow>
            {sensor.interfaces.length > 0 && (
              <DetailRow label={t("probes.interfaces.columns.name")}>
                <Mono>{sensor.interfaces.join(", ")}</Mono>
              </DetailRow>
            )}
            {sensor.helper_state !== null && sensor.helper_state !== "none" && (
              <DetailRow label={t("probes.sensorHelper")}>
                {sensor.helper_state === "listening" ? (
                  <Badge tone="ok">{t("sensors.helperState.listening")}</Badge>
                ) : (
                  <Badge tone="danger">
                    {t("sensors.helperState.inactive")}
                  </Badge>
                )}
              </DetailRow>
            )}
            {sensor.tool_name !== null && (
              <DetailRow label={t("probes.sensorTool.column")}>
                <span className="text-ink">{sensor.tool_name}</span>{" "}
                <Mono className="text-ink-3">
                  {sensor.installed_tool_version ?? "—"}
                </Mono>
              </DetailRow>
            )}
          </dl>
        </Card>
      ) : (
        <Card>
          <EmptyState title={t("probeSensor.notOnProbe")} />
        </Card>
      )}

      <PermissionGate permission="sensor_log.read">
        <RunsCard probe={account} sensor={sensorName ?? ""} />
      </PermissionGate>
    </div>
  );
}

function RunsCard({ probe, sensor }: { probe: string; sensor: string }) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useSensorLogTimeline(probe, sensor);
  const params = new URLSearchParams({ probe, sensor });
  return (
    <Card
      title={t("probeSensor.timelineTitle")}
      action={
        <Link
          to={`/sensor-logs?${params.toString()}`}
          className="text-accent text-xs hover:underline"
        >
          {t("sensorLogs.all")}
        </Link>
      }
    >
      {error ? (
        <ErrorDetails error={error} />
      ) : isLoading ? (
        <Skeleton className="h-24" />
      ) : !data || data.streams.length === 0 ? (
        <EmptyState title={t("sensorLogs.empty")} />
      ) : (
        <SensorTimelineRows timeline={data} />
      )}
    </Card>
  );
}
