/**
 * JSON tree-walker inference for HistGradientBoostingClassifier.
 *
 * Loads the exported model JSON and runs inference entirely in JS —
 * no ONNX runtime or Python backend required.
 */

export interface TreeNode {
  left: number;
  right: number;
  feature_idx: number;
  threshold: number;
  value: number;
  is_leaf: boolean;
  missing_go_to_left: boolean;
}

export interface CalibrationMap {
  x: number[];
  y: number[];
}

export interface ExportedModel {
  format: string;
  feature_names: string[];
  numeric_features: string[];
  categorical_features: string[];
  baseline_prediction: number;
  learning_rate: number;
  trees: TreeNode[][];
  calibration: CalibrationMap | null;
  metrics: {
    auc: number;
    ece: number;
    n_train: number;
    n_test: number;
  };
  feature_importances: Record<string, number>;
}

export interface CompanyFeatures {
  company_age_months?: number | null;
  employee_count?: number | null;
  revenue_at_raise?: number | null;
  pre_revenue?: number | null;
  total_assets?: number | null;
  total_debt?: number | null;
  debt_to_asset_ratio?: number | null;
  cash_position?: number | null;
  funding_target?: number | null;
  amount_raised?: number | null;
  overfunding_ratio?: number | null;
  instrument_type?: string | null;
  platform?: string | null;
  country?: string | null;
}

/**
 * Simple string hash matching Python's hash(str(val)) % 100_000.
 * Uses djb2 algorithm for deterministic cross-platform hashing.
 */
function hashCategory(val: string): number {
  let hash = 5381;
  for (let i = 0; i < val.length; i++) {
    hash = (hash * 33) ^ val.charCodeAt(i);
  }
  return ((hash >>> 0) % 100_000);
}

/** Walk a single tree to get its leaf value. */
function walkTree(nodes: TreeNode[], features: (number | null)[]): number {
  let idx = 0;
  while (!nodes[idx].is_leaf) {
    const node = nodes[idx];
    const featureVal = features[node.feature_idx];

    if (featureVal === null || featureVal === undefined || Number.isNaN(featureVal)) {
      // Missing value handling
      idx = node.missing_go_to_left ? node.left : node.right;
    } else if (featureVal <= node.threshold) {
      idx = node.left;
    } else {
      idx = node.right;
    }
  }
  return nodes[idx].value;
}

/** Linear interpolation for calibration mapping. */
function interpolate(x: number, xp: number[], yp: number[]): number {
  if (x <= xp[0]) return yp[0];
  if (x >= xp[xp.length - 1]) return yp[yp.length - 1];

  for (let i = 1; i < xp.length; i++) {
    if (x <= xp[i]) {
      const t = (x - xp[i - 1]) / (xp[i] - xp[i - 1]);
      return yp[i - 1] + t * (yp[i] - yp[i - 1]);
    }
  }
  return yp[yp.length - 1];
}

/** Sigmoid function. */
function sigmoid(x: number): number {
  return 1.0 / (1.0 + Math.exp(-x));
}

/**
 * Build a feature vector from company data, matching the Python
 * _build_feature_matrix ordering.
 */
function buildFeatureVector(
  model: ExportedModel,
  features: CompanyFeatures,
): (number | null)[] {
  const vec: (number | null)[] = [];

  for (const name of model.numeric_features) {
    const val = features[name as keyof CompanyFeatures];
    if (val === null || val === undefined) {
      vec.push(null);
    } else if (typeof val === "boolean") {
      vec.push(val ? 1.0 : 0.0);
    } else {
      vec.push(Number(val));
    }
  }

  for (const name of model.categorical_features) {
    const val = features[name as keyof CompanyFeatures];
    if (val === null || val === undefined) {
      vec.push(null);
    } else {
      vec.push(hashCategory(String(val)));
    }
  }

  return vec;
}

export interface InferenceResult {
  /** Probability of failure (0-1). */
  pFail: number;
  /** Score 0-100 where higher = more likely to survive. */
  score: number;
  /** Raw (uncalibrated) P(fail). */
  rawPFail: number;
}

/**
 * Run inference on a single company's features.
 *
 * Returns P(failure) and a 0-100 survival score.
 */
export function predict(
  model: ExportedModel,
  features: CompanyFeatures,
): InferenceResult {
  const vec = buildFeatureVector(model, features);

  // Sum tree predictions
  let rawPrediction = model.baseline_prediction;
  for (const tree of model.trees) {
    rawPrediction += model.learning_rate * walkTree(tree, vec);
  }

  // Convert to probability via sigmoid
  let pFail = sigmoid(rawPrediction);
  const rawPFail = pFail;

  // Apply calibration if available
  if (model.calibration) {
    pFail = interpolate(pFail, model.calibration.x, model.calibration.y);
  }

  // Clamp to valid range
  pFail = Math.max(0, Math.min(1, pFail));

  // Score: higher = better (less likely to fail)
  const score = Math.round((1.0 - pFail) * 100);

  return { pFail, score, rawPFail };
}

let cachedModel: ExportedModel | null = null;

/** Load the model from a JSON file (with caching). */
export async function loadModel(modelUrl: string): Promise<ExportedModel> {
  if (cachedModel) return cachedModel;

  const response = await fetch(modelUrl);
  if (!response.ok) {
    throw new Error(`Failed to load model: ${response.status}`);
  }
  cachedModel = (await response.json()) as ExportedModel;
  return cachedModel;
}
