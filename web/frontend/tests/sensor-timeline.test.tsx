import { QueryClient } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { SensorTimeline, SensorTimelineState } from "@/api/types";
import { AppProviders } from "@/app/providers";
import { SensorTimelineRows } from "@/features/sensorLogs/SensorTimeline";
import { changeLanguage } from "@/i18n";

/**
 * The timeline on a sensor's page: one bar per network on a shared axis,
 * with the figures that say at a glance whether the network is fine.
 */

function buckets(states: SensorTimelineState[]) {
  return states.map((state, index) => ({
    start: new Date(Date.UTC(2025, 2, 8, 0, index * 15)).toISOString(),
    ok: state === "none" ? 0 : 4,
    error: state === "failing" || state === "outlier" ? 1 : 0,
    state,
    code:
      state === "failing" || state === "outlier" ? "gateway-unreachable" : null,
  }));
}

const TIMELINE: SensorTimeline = {
  probe: "mpp-berlin-01",
  sensor: "wlan-auth",
  start: "2025-03-08T00:00:00Z",
  until: "2025-03-08T01:00:00Z",
  bucket_seconds: 900,
  retained_since: "2025-02-23T00:00:00Z",
  streams: [
    {
      ssid: "Example-Corp",
      ok: 98,
      error: 2,
      success_rate: 0.98,
      runs_per_hour: 52.4,
      last_failure: {
        id: "r1",
        probe: "mpp-berlin-01",
        sensor: "wlan-auth",
        job: null,
        time: "2025-03-08T00:32:00Z",
        result: "error",
        code: "gateway-unreachable",
        summary: "gateway no reply",
        ssid: "Example-Corp",
      },
      buckets: buckets(["ok", "outlier", "failing", "none"]),
    },
    {
      ssid: "Example-Guest",
      ok: 40,
      error: 0,
      success_rate: 1,
      runs_per_hour: 50,
      last_failure: null,
      buckets: buckets(["ok", "ok", "ok", "ok"]),
    },
  ],
};

function show(timeline: SensorTimeline) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <SensorTimelineRows timeline={timeline} />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("sensor timeline", () => {
  beforeEach(async () => {
    await changeLanguage("en");
  });

  it("draws one bar per network with its figures", () => {
    show(TIMELINE);
    const corp = screen.getByRole("img", {
      name: /Example-Corp: 98 OK, 2 failed/,
    });
    expect(corp.children).toHaveLength(4);
    expect(
      screen.getByRole("img", { name: /Example-Guest/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("98%")).toBeInTheDocument();
    expect(screen.getByText("52 sign-ins/h")).toBeInTheDocument();
    expect(
      screen.getByText(/last failure .* gateway-unreachable/),
    ).toBeInTheDocument();
    expect(screen.getByText("no failure")).toBeInTheDocument();
  });

  it("marks a single failure quietly and a lasting one in red", () => {
    show(TIMELINE);
    const [ok, outlier, failing, none] = Array.from(
      screen.getByRole("img", { name: /Example-Corp/ }).children,
    );
    expect(ok).toHaveClass("bg-ok/70");
    expect(outlier).toHaveClass("bg-ok/70");
    expect(outlier.querySelector(".bg-danger")).not.toBeNull();
    expect(failing).toHaveClass("bg-danger");
    expect(none).toHaveClass("bg-surface-2");
  });

  it("counts runs rather than sign-ins for other sensors", () => {
    show({
      ...TIMELINE,
      sensor: "aruba-uplink",
      streams: [{ ...TIMELINE.streams[1], ssid: null }],
    });
    expect(screen.getByText("50 runs/h")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /aruba-uplink/ }),
    ).toBeInTheDocument();
  });
});
