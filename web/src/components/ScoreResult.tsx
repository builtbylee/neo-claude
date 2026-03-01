"use client";

interface ScoreResultProps {
  result: {
    score: number;
    confidenceRange: number;
    recommendation: {
      class: string;
      label: string;
      description: string;
    };
    categories: Record<string, number>;
    dataCompleteness: number;
    textScores: Record<string, number> | null;
    gates: Array<{
      name: string;
      passed: boolean;
      action: string;
      reason: string;
    }>;
    matchedCompany: string | null;
  };
}

const REC_COLORS: Record<string, string> = {
  invest: "bg-green-600",
  deep_diligence: "bg-blue-600",
  watch: "bg-yellow-600",
  pass: "bg-red-600",
  abstain: "bg-neutral-600",
};

const REC_TEXT_COLORS: Record<string, string> = {
  invest: "text-green-400",
  deep_diligence: "text-blue-400",
  watch: "text-yellow-400",
  pass: "text-red-400",
  abstain: "text-neutral-400",
};

function ScoreGauge({ score, range }: { score: number; range: number }) {
  const angle = (score / 100) * 180 - 90;
  const color =
    score >= 65
      ? "#22c55e"
      : score >= 50
        ? "#3b82f6"
        : score >= 40
          ? "#eab308"
          : "#ef4444";

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-48 h-24 overflow-hidden">
        {/* Background arc */}
        <svg viewBox="0 0 200 100" className="w-full h-full">
          <path
            d="M 10 100 A 90 90 0 0 1 190 100"
            fill="none"
            stroke="#374151"
            strokeWidth="12"
            strokeLinecap="round"
          />
          <path
            d="M 10 100 A 90 90 0 0 1 190 100"
            fill="none"
            stroke={color}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={`${(score / 100) * 283} 283`}
          />
        </svg>
      </div>
      <div className="text-center -mt-4">
        <span className="text-4xl font-bold text-white">{score}</span>
        <span className="text-lg text-neutral-400">/100</span>
      </div>
      <div className="text-sm text-neutral-500 mt-1">
        +/- {range} points
      </div>
    </div>
  );
}

function CategoryBar({
  name,
  score,
}: {
  name: string;
  score: number;
}) {
  const color =
    score >= 60
      ? "bg-green-500"
      : score >= 45
        ? "bg-blue-500"
        : score >= 35
          ? "bg-yellow-500"
          : "bg-red-500";

  return (
    <div className="flex items-center gap-3">
      <span className="text-sm text-neutral-300 w-40 shrink-0">{name}</span>
      <div className="flex-1 bg-neutral-700 rounded-full h-2.5 overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all duration-500`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-sm text-neutral-400 w-8 text-right">{score}</span>
    </div>
  );
}

export default function ScoreResult({ result }: ScoreResultProps) {
  const recColor = REC_COLORS[result.recommendation.class] ?? "bg-neutral-600";
  const recTextColor =
    REC_TEXT_COLORS[result.recommendation.class] ?? "text-neutral-400";

  const failedGates = result.gates.filter((g) => !g.passed);

  return (
    <div className="space-y-6">
      {/* Header: Score + Recommendation */}
      <div className="flex flex-col sm:flex-row items-center gap-6">
        <ScoreGauge score={result.score} range={result.confidenceRange} />
        <div className="text-center sm:text-left">
          <span
            className={`inline-block px-3 py-1 rounded-full text-sm font-semibold text-white ${recColor}`}
          >
            {result.recommendation.label}
          </span>
          <p className="text-sm text-neutral-400 mt-2 max-w-sm">
            {result.recommendation.description}
          </p>
          {result.matchedCompany && (
            <p className="text-xs text-neutral-500 mt-1">
              Matched: {result.matchedCompany}
            </p>
          )}
        </div>
      </div>

      {/* Category Breakdown */}
      <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
        <h3 className="text-sm font-semibold text-neutral-300 mb-4">
          Category Breakdown
        </h3>
        <div className="space-y-3">
          {Object.entries(result.categories).map(([name, score]) => (
            <CategoryBar key={name} name={name} score={score} />
          ))}
        </div>
      </div>

      {/* Claude Text Analysis */}
      {result.textScores && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-4">
            AI Text Analysis
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Object.entries(result.textScores).map(([dim, score]) => (
              <div key={dim} className="text-center">
                <div className="text-lg font-bold text-white">{score}</div>
                <div className="text-xs text-neutral-500 capitalize">
                  {dim.replace(/_/g, " ")}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Gates */}
      {failedGates.length > 0 && (
        <div className="bg-red-950/30 rounded-xl p-5 border border-red-800/30">
          <h3 className="text-sm font-semibold text-red-400 mb-3">
            Gate Alerts
          </h3>
          <div className="space-y-2">
            {failedGates.map((gate) => (
              <div key={gate.name} className="flex items-start gap-2 text-sm">
                <span className="text-red-400 mt-0.5">x</span>
                <div>
                  <span className="text-neutral-300 font-medium">
                    {gate.name}:
                  </span>{" "}
                  <span className="text-neutral-400">{gate.reason}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Data Completeness */}
      <div className="flex items-center justify-between text-sm text-neutral-500 pt-2 border-t border-neutral-800">
        <span>Data completeness: {result.dataCompleteness}%</span>
        <span>
          {result.textScores ? "AI analysis included" : "No pitch text provided"}
        </span>
      </div>
    </div>
  );
}
