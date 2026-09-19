import { Activity, Loader2, Radar } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  CartesianGrid,
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar as RechartsRadar,
  RadarChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  XAxis,
  YAxis,
} from "recharts";
import { runCompare } from "@/api/hooks";
import type { Authorship, CompareResponse, JudgeAxes } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useEditor } from "@/state/editor";
import { jitter } from "@/lib/utils";

const GROUP_COLORS: Record<string, string> = {
  current: "#6ea8fe",
  draft: "#9d8cff",
  human: "#7ee2a8",
  ai: "#ff8f8f",
};
const GROUP_LABELS: Record<string, string> = {
  human: "Your posts",
  current: "This draft",
  draft: "Other drafts",
  ai: "AI controls",
};

function DiamondCentroid(props: { cx?: number; cy?: number; fill?: string }) {
  const { cx = 0, cy = 0, fill = "#fff" } = props;
  return (
    <g transform={`translate(${cx},${cy}) rotate(45)`}>
      <rect x={-5} y={-5} width={10} height={10} fill={fill} stroke="#0d1117" strokeWidth={1.5} />
    </g>
  );
}

export function ShapeTab() {
  const { markdown } = useEditor();
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [running, setRunning] = useState(false);

  const run = async () => {
    setRunning(true);
    try {
      setCompare(await runCompare(markdown));
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const axes = compare?.axes_names ?? [];
  const current = compare?.docs.find((d) => d.group === "current");
  const ai = compare?.docs.find((d) => d.group === "ai");
  const humans = compare?.docs.filter((d) => d.group === "human") ?? [];

  const radarData = axes.map((axis) => ({
    axis,
    current: current?.axes[axis] ?? 0,
    human: humans.length
      ? humans.reduce((s, d) => s + d.axes[axis], 0) / humans.length
      : undefined,
    ai: ai?.axes[axis] ?? 0,
  }));

  const groups = ["human", "current", "draft", "ai"];
  const rarityGroups = groups
    .map((g) => {
      const pts = (compare?.docs ?? []).filter((d) => d.group === g);
      if (!pts.length) return null;
      const rs = pts.map((d) => d.rarity).sort((a, b) => a - b);
      const mean = rs.reduce((s, r) => s + r, 0) / rs.length;
      const median = rs[Math.floor(rs.length / 2)];
      const gi = groups.indexOf(g);
      return {
        group: g,
        pts: pts.map((d) => ({ x: gi + jitter(d.label), y: d.rarity, label: d.label })),
        mean,
        median,
        gi,
      };
    })
    .filter((x): x is NonNullable<typeof x> => !!x);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-3">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" onClick={() => void run()} disabled={running || !markdown.trim()}>
          {running ? <Loader2 size={13} className="animate-spin" /> : <Activity size={13} />}
          Run shape analysis
        </Button>
        <span className="text-[11px] text-muted">judge + embeddings + graphs</span>
      </div>

      {compare && <JudgeLine judge={compare.judge} authorship={compare.authorship} />}

      {compare && (
        <>
          <section>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
              Narrative rarity percentile
            </h3>
            <ResponsiveContainer width="100%" height={170}>
              <ScatterChart margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--line)" />
                <XAxis
                  type="number"
                  domain={[-0.5, groups.length - 0.5]}
                  ticks={groups.map((_, i) => i)}
                  tickFormatter={(v: number) => GROUP_LABELS[groups[v]] ?? ""}
                  tick={{ fontSize: 9 }}
                />
                <YAxis type="number" domain={[-0.05, 1.05]} tick={{ fontSize: 9 }} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
                {rarityGroups.map((g) => (
                  <ReferenceLine
                    key={`${g.group}-mean`}
                    segment={[
                      { x: g.gi - 0.32, y: g.mean },
                      { x: g.gi + 0.32, y: g.mean },
                    ]}
                    stroke={GROUP_COLORS[g.group]}
                    strokeWidth={2}
                  />
                ))}
                {rarityGroups.map((g) => (
                  <ReferenceLine
                    key={`${g.group}-median`}
                    segment={[
                      { x: g.gi - 0.32, y: g.median },
                      { x: g.gi + 0.32, y: g.median },
                    ]}
                    stroke={GROUP_COLORS[g.group]}
                    strokeWidth={1.5}
                    strokeDasharray="5 4"
                  />
                ))}
                {rarityGroups.map((g) => (
                  <Scatter
                    key={g.group}
                    name={GROUP_LABELS[g.group]}
                    data={g.pts}
                    fill={GROUP_COLORS[g.group]}
                    fillOpacity={0.85}
                  />
                ))}
                <Legend wrapperStyle={{ fontSize: 10 }} />
              </ScatterChart>
            </ResponsiveContainer>
          </section>

          <section>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
              Narrative space (centroids ◆)
            </h3>
            <ResponsiveContainer width="100%" height={190}>
              <ScatterChart margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--line)" />
                <XAxis type="number" dataKey="x" tick={{ fontSize: 9 }} label={{ value: "PC1", position: "insideBottom", offset: -2, fontSize: 9 }} />
                <YAxis type="number" dataKey="y" tick={{ fontSize: 9 }} label={{ value: "PC2", angle: -90, position: "insideLeft", fontSize: 9 }} />
                {groups.map((g) => {
                  const pts = compare.docs.filter((d) => d.group === g);
                  if (!pts.length) return null;
                  const cx = pts.reduce((s, d) => s + d.x, 0) / pts.length;
                  const cy = pts.reduce((s, d) => s + d.y, 0) / pts.length;
                  return (
                    <Scatter
                      key={`centroid-${g}`}
                      name={`${GROUP_LABELS[g]} ◆`}
                      data={[{ x: cx, y: cy }]}
                      shape={<DiamondCentroid fill={GROUP_COLORS[g]} />}
                      isAnimationActive={false}
                    />
                  );
                })}
                {groups.map((g) => (
                  <Scatter
                    key={g}
                    name={GROUP_LABELS[g]}
                    data={compare.docs.filter((d) => d.group === g)}
                    fill={GROUP_COLORS[g]}
                    fillOpacity={0.65}
                  />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </section>

          <section>
            <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">
              <Radar size={11} /> Shape profile ({axes.length} axes)
            </h3>
            <ResponsiveContainer width="100%" height={230}>
              <RadarChart data={radarData} outerRadius="72%">
                <PolarGrid stroke="var(--line)" />
                <PolarAngleAxis dataKey="axis" tick={{ fontSize: 9 }} />
                <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
                {humans.length > 0 && (
                  <RechartsRadar
                    name="your posts (mean)"
                    dataKey="human"
                    stroke={GROUP_COLORS.human}
                    fill={GROUP_COLORS.human}
                    fillOpacity={0.1}
                  />
                )}
                <RechartsRadar
                  name="AI control"
                  dataKey="ai"
                  stroke={GROUP_COLORS.ai}
                  fill="none"
                  strokeDasharray="5 4"
                />
                <RechartsRadar
                  name="this draft"
                  dataKey="current"
                  stroke={GROUP_COLORS.current}
                  fill={GROUP_COLORS.current}
                  fillOpacity={0.15}
                />
                <Legend wrapperStyle={{ fontSize: 10 }} />
              </RadarChart>
            </ResponsiveContainer>
          </section>
        </>
      )}

      {!compare && (
        <p className="pt-4 text-center text-[12.5px] leading-6 text-muted">
          StoryScope-style shape audit: discourse judge, embedding glide,
          novelty, rarity, and the narrative-space graph vs your references
          and AI controls. Drop posts into{" "}
          <code className="rounded bg-panel2 px-1">references/</code> to light
          up the human overlays.
        </p>
      )}
    </div>
  );
}

function JudgeLine({ judge, authorship }: { judge: JudgeAxes | null; authorship: Authorship | null }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-line bg-panel2 p-2.5 text-[11.5px] leading-5">
      {authorship ? (
        <div className="flex items-center gap-2">
          <Badge tone={authorship.verdict === "human-side" ? "ok" : "err"}>
            {authorship.verdict === "human-side" ? "✓" : "⚠"} {authorship.verdict}
          </Badge>
          <span className="text-muted">
            dist-to-you {authorship.human_dist} vs dist-to-AI {authorship.ai_dist} (ratio{" "}
            {authorship.ratio})
          </span>
        </div>
      ) : (
        <span className="text-muted">
          authorship: add posts to references/ to enable
        </span>
      )}
      {judge ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted">
          {Object.entries(judge)
            .filter(([k]) => k !== "notes")
            .map(([k, v]) => (
              <span key={k}>
                {k}{" "}
                <span className={v >= 0.7 ? "font-semibold text-err" : "text-text"}>
                  {v.toFixed(2)}
                </span>
                {v >= 0.7 ? " ⚑" : ""}
              </span>
            ))}
        </div>
      ) : (
        <span className="text-muted">judge: unavailable</span>
      )}
    </div>
  );
}
